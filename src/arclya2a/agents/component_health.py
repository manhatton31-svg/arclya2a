"""Component health checks for production launch (email, crypto, public URL)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.email_delivery import effective_email_delivery_mode, normalize_delivery_mode
from arclya2a.payments.crypto import crypto_payments_summary
from arclya2a.settings import get_settings, public_url_source, resolve_public_base_url


def build_email_component_health() -> dict[str, Any]:
    """
    Email delivery readiness for agent verification.

    Production: ARCLYA_AGENT_EMAIL_DELIVERY=auto + SMTP URL + FROM address.
    Dev/CI: outbox mode (no SMTP required).
    """
    settings = get_settings()
    mode_configured = normalize_delivery_mode(settings.agent_email_delivery)
    mode_effective = effective_email_delivery_mode()
    smtp_configured = bool(settings.agent_email_smtp_url and settings.agent_email_from)
    public_url = resolve_public_base_url()
    url_source = public_url_source()

    issues: list[str] = []
    launch_ready = True
    status = "healthy"

    if mode_effective == "outbox":
        if mode_configured == "outbox":
            status = "dev_mode"
            launch_ready = False
            issues.append("ARCLYA_AGENT_EMAIL_DELIVERY=outbox — verification emails logged locally only")
        else:
            status = "not_configured"
            launch_ready = False
            issues.append(
                "Configure ARCLYA_AGENT_EMAIL_SMTP_URL and ARCLYA_AGENT_EMAIL_FROM for SMTP delivery"
            )
    elif mode_configured == "smtp" and not smtp_configured:
        status = "misconfigured"
        launch_ready = False
        issues.append("ARCLYA_AGENT_EMAIL_DELIVERY=smtp but SMTP URL or FROM address is missing")

    if url_source in ("request_host", "config/core.json"):
        issues.append(
            "Set ARCLYA_PUBLIC_URL (or rely on RENDER_EXTERNAL_URL) so verification links use the canonical domain"
        )
        if url_source == "request_host":
            launch_ready = False

    return {
        "status": status,
        "launch_ready": launch_ready,
        "delivery_mode_configured": mode_configured,
        "delivery_mode_effective": mode_effective,
        "smtp_configured": smtp_configured,
        "from_address_set": bool(settings.agent_email_from),
        "public_url": public_url,
        "public_url_source": url_source,
        "issues": issues,
    }


def _payments_last_24h(payments: list[dict[str, Any]]) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    for row in payments:
        ts = row.get("created_at") or row.get("updated_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        st = str(row.get("status", "unknown"))
        by_status[st] = by_status.get(st, 0) + 1
        if len(recent) < 5:
            recent.append(
                {
                    "payment_id": row.get("payment_id"),
                    "status": st,
                    "amount": row.get("amount"),
                    "network": row.get("network"),
                    "created_at": ts,
                }
            )
    return {
        "count": sum(by_status.values()),
        "by_status": by_status,
        "recent": recent,
    }


def build_crypto_component_health(root: Path) -> dict[str, Any]:
    """Crypto checkout readiness and recent payment activity."""
    summary = crypto_payments_summary(root)
    enabled = bool(summary.get("enabled"))
    configured = bool(summary.get("configured"))
    payments = summary.get("recent_payments") or []
    if summary.get("payment_count", 0) > len(payments):
        from arclya2a.payments.crypto import list_crypto_payments

        payments = list_crypto_payments(root, limit=200)

    issues: list[str] = []
    launch_ready = True
    status = "healthy"

    if not enabled:
        status = "disabled"
        launch_ready = False
        issues.append("Set ARCLYA_CRYPTO_ENABLED=1 to accept USDC checkout")
    elif not configured:
        status = "not_configured"
        launch_ready = False
        issues.append("Set ARCLYA_CRYPTO_WALLET_* receive addresses for at least one network")

    activity_24h = _payments_last_24h(payments if isinstance(payments, list) else [])

    return {
        "status": status,
        "launch_ready": launch_ready,
        "enabled": enabled,
        "configured": configured,
        "networks": summary.get("networks", []),
        "default_network": summary.get("network"),
        "token": summary.get("token"),
        "payment_count": summary.get("payment_count", 0),
        "pending_review_count": summary.get("pending_review_count", 0),
        "confirmed_total_usd": summary.get("confirmed_total_usd", 0),
        "activity_24h": activity_24h,
        "issues": issues,
    }


def build_component_health(root: Path) -> dict[str, Any]:
    """Aggregate component health for /status and launch readiness."""
    email = build_email_component_health()
    crypto = build_crypto_component_health(root)
    launch_ready = email.get("launch_ready") and crypto.get("launch_ready")
    blocking = list(email.get("issues", [])) + list(crypto.get("issues", []))

    overall = "ready"
    if not launch_ready:
        overall = "pending_configuration"
    if email.get("status") == "misconfigured" or crypto.get("status") == "not_configured":
        overall = "action_required"

    return {
        "overall": overall,
        "launch_ready": launch_ready,
        "email": email,
        "crypto": crypto,
        "blocking_issues": blocking,
    }