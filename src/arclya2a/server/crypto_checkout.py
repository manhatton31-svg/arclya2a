"""HTTP routes for crypto checkout with x402 Payment Required support."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from arclya2a.payments.crypto import (
    STATUS_CONFIRMED,
    STATUS_SUBMITTED,
    confirm_crypto_payment,
    create_crypto_payment_intent,
    get_crypto_payment,
    is_crypto_payments_configured,
    is_crypto_payments_enabled,
    list_accepted_crypto_networks,
    update_crypto_payment,
)
from arclya2a.payments.packages import (
    AGENT_PAYMENTS_DOC_URL,
    USDC_NETWORK_LABELS,
    USDC_NETWORKS_SUMMARY,
    build_package_checkout_instructions,
    get_payment_package,
    list_payment_packages,
    package_public_view,
)
from arclya2a.server.operator_auth import load_operator_key, verify_operator_key
from arclya2a.payments.x402 import (
    apply_x402_headers,
    build_checkout_body,
    parse_x_payment_header,
    payment_requires_settlement,
    x402_response,
)
from arclya2a.server.errors import json_error
from arclya2a.server.schemas import (
    CryptoPaymentCheckoutRequest,
    CryptoPaymentConfirmRequest,
    CryptoPaymentIntentRequest,
    CryptoPaymentSubmitRequest,
)
from fastapi.responses import JSONResponse


def _crypto_unavailable_response():
    return json_error(
        code="crypto_not_configured",
        message="Crypto payments are not configured on this server",
        status_code=503,
    )


def _package_checkout_response(
    payment: dict[str, Any],
    package: dict[str, Any],
    *,
    resource: str,
    status_code: int,
    base_url: str,
) -> JSONResponse:
    """x402 checkout response enriched with package details and step-by-step instructions."""
    payment_required = payment_requires_settlement(str(payment.get("status", "")))
    body = build_checkout_body(payment, payment_required=payment_required)
    body["package"] = package_public_view(package)
    body["instructions"] = build_package_checkout_instructions(
        payment, package, base_url=base_url
    )
    response = JSONResponse(status_code=status_code, content=body)
    return apply_x402_headers(
        response,
        payment,
        resource=resource,
        payment_required=payment_required,
    )


def register_crypto_checkout_routes(router: APIRouter) -> None:
    """Register crypto checkout endpoints on the given router."""

    @router.get("/payments/crypto/networks")
    async def crypto_networks() -> dict[str, Any]:
        """List accepted USDC networks and receive addresses from configuration."""
        if not is_crypto_payments_configured():
            return json_error(
                code="crypto_not_configured",
                message="Crypto payments are not configured on this server",
                status_code=503,
            )
        return {
            "enabled": is_crypto_payments_enabled(),
            "token": "USDC",
            "summary": USDC_NETWORKS_SUMMARY,
            "recommended_network": "base",
            "network_labels": USDC_NETWORK_LABELS,
            "networks": list_accepted_crypto_networks(),
            "documentation": AGENT_PAYMENTS_DOC_URL,
        }

    @router.get("/payments/crypto/packages")
    async def crypto_payment_packages(request: Request) -> dict[str, Any]:
        """List USDC service packages agents can purchase (self-service checkout)."""
        if not is_crypto_payments_configured():
            return _crypto_unavailable_response()
        root = request.app.state.root
        base_url = str(request.base_url).rstrip("/")
        catalog_packages = list_payment_packages(root)
        return {
            "enabled": is_crypto_payments_enabled(),
            "currency": "USDC",
            "summary": USDC_NETWORKS_SUMMARY,
            "recommended_network": "base",
            "network_labels": USDC_NETWORK_LABELS,
            "packages": [package_public_view(p) for p in catalog_packages],
            "networks": list_accepted_crypto_networks(),
            "checkout_url": f"{base_url}/payments/crypto/checkout",
            "intent_url": f"{base_url}/payments/crypto/intent",
            "documentation": AGENT_PAYMENTS_DOC_URL,
        }

    @router.post("/payments/crypto/checkout")
    async def create_crypto_checkout(
        request: Request,
        body: CryptoPaymentCheckoutRequest,
    ) -> Any:
        """Create a package-based USDC checkout with clear payment instructions."""
        root = request.app.state.root
        if not is_crypto_payments_configured():
            return _crypto_unavailable_response()
        if not is_crypto_payments_enabled():
            return json_error(
                code="crypto_disabled",
                message="Crypto payments are disabled (set ARCLYA_CRYPTO_ENABLED=1)",
                status_code=503,
            )

        package_key = body.package or body.service_type
        if not package_key:
            return json_error(
                code="missing_package",
                message="Provide package or service_type (onboarding_package, closer_access, per_close)",
                status_code=400,
            )
        package = get_payment_package(package_key, root)
        if not package:
            supported = ", ".join(p.get("id", "") for p in list_payment_packages(root))
            return json_error(
                code="unknown_package",
                message=f"Unknown package '{package_key}'. Supported: {supported}",
                status_code=400,
            )

        amount_usd = float(package.get("amount_usd", 0))
        package_id = package.get("id", "")
        metadata: dict[str, Any] = {
            "package_id": package_id,
            "package_name": package.get("name"),
            "service_type": package_id,
        }
        if body.agent_id:
            metadata["agent_id"] = body.agent_id

        try:
            intent = create_crypto_payment_intent(
                root,
                amount_usd=amount_usd,
                network=body.network,
                partner_id=body.partner_id,
                deal_id=body.deal_id,
                customer_ref=body.customer_ref,
                memo=f"Arclya {package.get('name', package_id)}",
                metadata=metadata,
            )
        except ValueError as exc:
            return json_error(
                code="invalid_payment_request",
                message=str(exc),
                status_code=400,
            )

        payment = get_crypto_payment(root, intent.payment_id)
        if not payment:
            raise HTTPException(status_code=500, detail="Payment record missing after checkout")

        resource = f"/payments/crypto/{payment['payment_id']}"
        base_url = str(request.base_url).rstrip("/")
        prefer_402 = request.headers.get("X-Arclya-Prefer-402", "").lower() in (
            "1",
            "true",
            "yes",
        )
        status_code = 402 if prefer_402 else 201
        return _package_checkout_response(
            payment,
            package,
            resource=resource,
            status_code=status_code,
            base_url=base_url,
        )

    @router.post("/payments/crypto/intent")
    async def create_crypto_intent(
        request: Request,
        body: CryptoPaymentIntentRequest,
    ) -> Any:
        """Create a crypto payment intent. Returns 201 or 402 with x402 headers."""
        root = request.app.state.root
        if not is_crypto_payments_configured():
            return json_error(
                code="crypto_not_configured",
                message="Crypto payments are not configured",
                status_code=503,
            )
        if not is_crypto_payments_enabled():
            return json_error(
                code="crypto_disabled",
                message="Crypto payments are disabled (set ARCLYA_CRYPTO_ENABLED=1)",
                status_code=503,
            )

        metadata: dict[str, Any] | None = None
        if body.agent_id:
            metadata = {"agent_id": body.agent_id}
        if body.package:
            package = get_payment_package(body.package, root)
            if package:
                merged = dict(metadata or {})
                merged.update({
                    "package_id": package.get("id"),
                    "package_name": package.get("name"),
                    "service_type": package.get("id"),
                })
                metadata = merged

        try:
            intent = create_crypto_payment_intent(
                root,
                amount_usd=body.amount,
                network=body.network,
                partner_id=body.partner_id,
                deal_id=body.deal_id,
                customer_ref=body.customer_ref,
                memo=body.memo,
                metadata=metadata,
            )
        except ValueError as exc:
            return json_error(
                code="invalid_payment_request",
                message=str(exc),
                status_code=400,
            )

        payment = get_crypto_payment(root, intent.payment_id)
        if not payment:
            raise HTTPException(status_code=500, detail="Payment record missing after intent creation")

        resource = f"/payments/crypto/{payment['payment_id']}"
        base_url = str(request.base_url).rstrip("/")
        prefer_402 = request.headers.get("X-Arclya-Prefer-402", "").lower() in (
            "1",
            "true",
            "yes",
        )
        status_code = 402 if prefer_402 else 201

        if body.package:
            package = get_payment_package(body.package, root)
            if package:
                return _package_checkout_response(
                    payment,
                    package,
                    resource=resource,
                    status_code=status_code,
                    base_url=base_url,
                )

        return x402_response(
            payment,
            resource=resource,
            status_code=status_code,
            payment_required=True,
        )

    @router.get("/payments/crypto/{payment_id}")
    async def get_crypto_payment_status(request: Request, payment_id: str) -> Any:
        """Return payment status. Responds with 402 when payment is still required."""
        root = request.app.state.root
        payment = get_crypto_payment(root, payment_id)
        if not payment:
            return json_error(
                code="payment_not_found",
                message=f"Payment not found: {payment_id}",
                status_code=404,
            )

        resource = f"/payments/crypto/{payment_id}"
        status = str(payment.get("status", ""))
        if payment_requires_settlement(status):
            return x402_response(
                payment,
                resource=resource,
                status_code=402,
                payment_required=True,
            )
        return x402_response(
            payment,
            resource=resource,
            status_code=200,
            payment_required=False,
        )

    @router.post("/payments/crypto/{payment_id}/submit")
    async def submit_crypto_payment_proof(
        request: Request,
        payment_id: str,
        body: CryptoPaymentSubmitRequest | None = None,
    ) -> Any:
        """Submit on-chain tx_hash proof via body or X-Payment / PAYMENT-SIGNATURE header."""
        root = request.app.state.root
        payment = get_crypto_payment(root, payment_id)
        if not payment:
            return json_error(
                code="payment_not_found",
                message=f"Payment not found: {payment_id}",
                status_code=404,
            )

        tx_hash: str | None = None
        header_proof = parse_x_payment_header(
            request.headers.get("X-Payment")
            or request.headers.get("PAYMENT-SIGNATURE")
        )
        if header_proof:
            tx_hash = str(header_proof.get("tx_hash") or header_proof.get("transaction_hash") or "")
        if body and body.tx_hash:
            tx_hash = body.tx_hash
        if not tx_hash:
            return json_error(
                code="missing_payment_proof",
                message="Provide tx_hash in JSON body or X-Payment header",
                status_code=400,
            )

        try:
            updated = update_crypto_payment(
                root,
                payment_id,
                status=STATUS_SUBMITTED,
                tx_hash=tx_hash.strip(),
                metadata={"submitted_via": "x402_checkout"},
            )
        except KeyError:
            return json_error(
                code="payment_not_found",
                message=f"Payment not found: {payment_id}",
                status_code=404,
            )
        except ValueError as exc:
            return json_error(
                code="invalid_payment_update",
                message=str(exc),
                status_code=400,
            )

        resource = f"/payments/crypto/{payment_id}"
        return x402_response(
            updated,
            resource=resource,
            status_code=200,
            payment_required=True,
            include_settlement=True,
        )

    @router.post("/payments/crypto/{payment_id}/confirm")
    async def confirm_crypto_payment_operator(
        request: Request,
        payment_id: str,
        body: CryptoPaymentConfirmRequest | None = None,
    ) -> Any:
        """Operator-only: confirm payment after manual on-chain verification."""
        if not verify_operator_key(request, configured_key=load_operator_key()):
            return json_error(
                code="operator_authentication_error",
                message="Valid operator key required (X-Arclya-Operator-Key)",
                status_code=401,
            )

        root = request.app.state.root
        payment = get_crypto_payment(root, payment_id)
        if not payment:
            return json_error(
                code="payment_not_found",
                message=f"Payment not found: {payment_id}",
                status_code=404,
            )

        if payment.get("status") == STATUS_CONFIRMED:
            return {
                "payment": payment,
                "duplicate": True,
                "message": "Payment already confirmed",
            }

        confirmed_by = (
            (body.confirmed_by if body else None)
            or request.headers.get("X-Arclya-Operator-Id", "").strip()
            or "operator"
        )
        tx_hash = body.tx_hash if body else None

        try:
            result = confirm_crypto_payment(
                root,
                payment_id,
                confirmed_by=confirmed_by,
                tx_hash=tx_hash,
            )
        except KeyError:
            return json_error(
                code="payment_not_found",
                message=f"Payment not found: {payment_id}",
                status_code=404,
            )
        except ValueError as exc:
            return json_error(
                code="invalid_payment_update",
                message=str(exc),
                status_code=400,
            )

        resource = f"/payments/crypto/{payment_id}"
        return x402_response(
            result,
            resource=resource,
            status_code=200,
            payment_required=False,
            include_settlement=True,
        )