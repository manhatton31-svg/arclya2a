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


X402_FACILITATORS: list[dict[str, Any]] = [
    {
        "id": "arclya-native",
        "name": "Arclya Direct Settlement",
        "type": "direct",
        "x402Version": 2,
        "schemes": ["exact"],
        "networks": list(X402_NETWORK_IDS.values()),
        "routing": "direct_to_platform_wallet",
    },
    {
        "id": "arclya-batch",
        "name": "Arclya Batch Settlement",
        "type": "batch_settlement",
        "x402Version": 2,
        "schemes": ["exact", "batch"],
        "max_batch_size": 50,
        "routing": "aggregate_then_settle",
    },
    {
        "id": "arclya-deferred",
        "name": "Arclya Deferred Settlement",
        "type": "deferred",
        "x402Version": 2,
        "schemes": ["exact", "deferred"],
        "routing": "authorize_now_settle_later",
    },
]


def list_x402_facilitators() -> list[dict[str, Any]]:
    return [dict(f) for f in X402_FACILITATORS]


def build_deferred_payment_payload(
    payment: dict[str, Any],
    *,
    resource: str,
    settle_after_hours: int = 24,
    facilitator_id: str = "arclya-deferred",
) -> dict[str, Any]:
    """x402 V2 deferred payment — authorize now, settle later."""
    base = build_payment_required_payload(payment, resource=resource)
    base["settlementMode"] = "deferred"
    base["facilitator"] = facilitator_id
    base["deferredSettlement"] = {
        "settle_after_hours": settle_after_hours,
        "status": "authorized",
        "payment_id": payment.get("payment_id"),
    }
    if base.get("accepts"):
        for accept in base["accepts"]:
            accept["settlementMode"] = "deferred"
            accept["facilitator"] = facilitator_id
            accept["extra"] = dict(accept.get("extra") or {})
            accept["extra"]["settle_after_hours"] = settle_after_hours
    return base


def build_batch_settlement_payload(
    payments: list[dict[str, Any]],
    *,
    facilitator_id: str = "arclya-batch",
    batch_id: str,
) -> dict[str, Any]:
    """x402 V2 batch settlement for multiple open payments."""
    total = sum(float(p.get("amount", p.get("amount_usd", 0))) for p in payments)
    currency = payments[0].get("currency", "USDC") if payments else "USDC"
    return {
        "x402Version": 2,
        "settlementMode": "batch",
        "facilitator": facilitator_id,
        "batch_id": batch_id,
        "payment_count": len(payments),
        "total_amount": round(total, 2),
        "currency": currency,
        "payments": [
            {
                "payment_id": p.get("payment_id"),
                "amount": p.get("amount", p.get("amount_usd")),
                "status": p.get("status"),
                "resource": f"/payments/crypto/{p.get('payment_id')}",
            }
            for p in payments
        ],
        "submit_url": "/payments/crypto/x402/batch-settle",
    }


def apply_facilitator_routing(
    payment_required: dict[str, Any],
    *,
    facilitator_id: str,
) -> dict[str, Any]:
    """Attach facilitator routing metadata to an x402 PaymentRequired payload."""
    facilitator = next((f for f in X402_FACILITATORS if f["id"] == facilitator_id), None)
    routed = dict(payment_required)
    routed["facilitator"] = facilitator_id
    if facilitator:
        routed["facilitator_details"] = {
            "name": facilitator.get("name"),
            "type": facilitator.get("type"),
            "routing": facilitator.get("routing"),
        }
    return routed


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