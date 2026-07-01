"""x402 Payment Required helpers for crypto checkout HTTP responses."""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi.responses import JSONResponse

from arclya2a.payments.crypto import STATUS_CONFIRMED, STATUS_PENDING, STATUS_SUBMITTED

# Arclya network id → x402 CAIP-style network identifiers (mainnet)
X402_NETWORK_IDS: dict[str, str] = {
    "base": "eip155:8453",
    "ethereum": "eip155:1",
    "bnb": "eip155:56",
    "solana": "solana:mainnet",
}

SETTLED_STATUSES = frozenset({STATUS_CONFIRMED})
OPEN_STATUSES = frozenset({STATUS_PENDING, STATUS_SUBMITTED})


def payment_requires_settlement(status: str) -> bool:
    return status in OPEN_STATUSES


def _amount_display(amount: float, currency: str) -> str:
    return f"${amount:.2f} {currency}"


def build_payment_required_payload(
    payment: dict[str, Any],
    *,
    resource: str,
    description: str = "Arclya crypto payment",
) -> dict[str, Any]:
    """Build x402 V2 PaymentRequired-compatible JSON."""
    network = payment.get("network", "")
    x402_network = X402_NETWORK_IDS.get(network, network)
    amount = float(payment.get("amount", payment.get("amount_usd", 0)))
    currency = payment.get("currency", payment.get("token", "USDC"))
    return {
        "x402Version": 2,
        "error": "payment_required",
        "accepts": [
            {
                "scheme": "exact",
                "network": x402_network,
                "arclyaNetwork": network,
                "price": _amount_display(amount, currency),
                "amount": str(amount),
                "currency": currency,
                "payTo": payment.get("wallet_address"),
                "resource": resource,
                "description": description,
                "mimeType": "application/json",
                "extra": {
                    "payment_id": payment.get("payment_id"),
                    "intent_id": payment.get("intent_id"),
                    "memo": payment.get("memo"),
                    "expires_at": payment.get("expires_at"),
                    "submit_url": f"/payments/crypto/{payment.get('payment_id')}/submit",
                },
            }
        ],
    }


def build_payment_response_payload(payment: dict[str, Any]) -> dict[str, Any]:
    """Build x402 settlement response payload after proof submission."""
    return {
        "x402Version": 2,
        "success": payment.get("status") in SETTLED_STATUSES,
        "status": payment.get("status"),
        "payment_id": payment.get("payment_id"),
        "tx_hash": payment.get("tx_hash"),
        "confirmed_at": payment.get("confirmed_at"),
        "message": (
            "Payment confirmed"
            if payment.get("status") == STATUS_CONFIRMED
            else "Payment proof received; awaiting confirmation"
        ),
    }


def build_checkout_body(payment: dict[str, Any], *, payment_required: bool) -> dict[str, Any]:
    """Standard JSON body for checkout endpoints."""
    return {
        "payment_required": payment_required,
        "payment": {
            "payment_id": payment.get("payment_id"),
            "intent_id": payment.get("intent_id"),
            "status": payment.get("status"),
            "amount": payment.get("amount", payment.get("amount_usd")),
            "currency": payment.get("currency", payment.get("token", "USDC")),
            "network": payment.get("network"),
            "wallet_address": payment.get("wallet_address"),
            "memo": payment.get("memo"),
            "tx_hash": payment.get("tx_hash"),
            "partner_id": payment.get("partner_id"),
            "deal_id": payment.get("deal_id"),
            "created_at": payment.get("created_at"),
            "updated_at": payment.get("updated_at"),
            "expires_at": payment.get("expires_at"),
            "confirmed_at": payment.get("confirmed_at"),
        },
        "x402": build_payment_required_payload(
            payment,
            resource=f"/payments/crypto/{payment.get('payment_id')}",
        )
        if payment_required
        else None,
        "instructions": {
            "send": f"Send {payment.get('amount')} {payment.get('currency', 'USDC')} "
            f"on {payment.get('network')} to {payment.get('wallet_address')}",
            "include_memo": payment.get("memo"),
            "submit_proof": f"POST /payments/crypto/{payment.get('payment_id')}/submit",
        },
    }


def apply_x402_headers(
    response: JSONResponse,
    payment: dict[str, Any],
    *,
    resource: str,
    payment_required: bool,
    include_settlement: bool = False,
) -> JSONResponse:
    """Attach x402 and Arclya payment headers to an HTTP response."""
    amount = float(payment.get("amount", payment.get("amount_usd", 0)))
    currency = payment.get("currency", payment.get("token", "USDC"))
    network = payment.get("network", "")
    required_payload = build_payment_required_payload(payment, resource=resource)

    response.headers["X-Payment-Id"] = str(payment.get("payment_id", ""))
    response.headers["X-Payment-Amount"] = str(amount)
    response.headers["X-Payment-Currency"] = currency
    response.headers["X-Payment-Network"] = network
    response.headers["X-Payment-Address"] = str(payment.get("wallet_address", ""))
    if payment.get("memo"):
        response.headers["X-Payment-Memo"] = str(payment["memo"])
    response.headers["X-Payment-Status"] = str(payment.get("status", ""))

    if payment_required:
        response.headers["X-Payment-Required"] = "true"
        required_json = json.dumps(required_payload, separators=(",", ":"))
        response.headers["X-Payment-Required-Details"] = required_json
        encoded = base64.b64encode(required_json.encode("utf-8")).decode("ascii")
        response.headers["PAYMENT-REQUIRED"] = encoded
    else:
        response.headers["X-Payment-Required"] = "false"

    if include_settlement:
        settlement = build_payment_response_payload(payment)
        settlement_json = json.dumps(settlement, separators=(",", ":"))
        response.headers["PAYMENT-RESPONSE"] = base64.b64encode(
            settlement_json.encode("utf-8")
        ).decode("ascii")

    return response


def x402_response(
    payment: dict[str, Any],
    *,
    resource: str,
    status_code: int,
    payment_required: bool | None = None,
    include_settlement: bool = False,
) -> JSONResponse:
    """Create a JSON response with x402 headers for crypto checkout."""
    if payment_required is None:
        payment_required = payment_requires_settlement(str(payment.get("status", "")))

    body = build_checkout_body(payment, payment_required=payment_required)
    response = JSONResponse(status_code=status_code, content=body)
    return apply_x402_headers(
        response,
        payment,
        resource=resource,
        payment_required=payment_required,
        include_settlement=include_settlement,
    )


def parse_x_payment_header(value: str | None) -> dict[str, Any] | None:
    """Parse X-Payment or PAYMENT-SIGNATURE header (JSON or base64 JSON)."""
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None