"""Public platform status summary for external agent accounts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import count_agent_accounts
from arclya2a.agents.audit import build_agent_audit_summary
from arclya2a.agents.component_health import build_component_health
from arclya2a.agents.email_delivery import effective_email_delivery_mode
from arclya2a.agents.email_verification import (
    directory_requires_email_verification,
    verification_token_hours,
)
from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.agents.security import (
    agent_directory_rate_limit_per_minute,
    agent_max_register_per_ip_per_day,
    agent_recommended_rate_limit_per_minute,
    agent_register_rate_limit_per_minute,
    agent_rotate_key_rate_limit_per_minute,
)
from arclya2a.agents.terms import current_terms_version
from arclya2a.settings import public_url_source, resolve_public_base_url


def build_agent_platform_status(root: Path) -> dict[str, Any]:
    """Aggregate external-agent platform metrics for /status and /health."""
    counts = count_agent_accounts(root)
    audit = build_agent_audit_summary(root, recent_limit=5)
    counts_24h = audit.get("counts_24h", {})

    return {
        "status": "available",
        "onboarding_guide_version": GUIDE_VERSION,
        "terms_version": current_terms_version(),
        "email_verification": {
            "required_for_directory": directory_requires_email_verification(),
            "token_expiry_hours": verification_token_hours(),
            "delivery_mode": effective_email_delivery_mode(),
            "verify_endpoint": "POST /agents/verify-email",
            "resend_endpoint": "POST /agents/me/resend-verification",
        },
        "accounts": counts,
        "rate_limits": {
            "register_per_minute": agent_register_rate_limit_per_minute(),
            "directory_per_minute": agent_directory_rate_limit_per_minute(),
            "recommended_per_minute": agent_recommended_rate_limit_per_minute(),
            "rotate_key_per_minute": agent_rotate_key_rate_limit_per_minute(),
            "max_register_per_ip_per_day": agent_max_register_per_ip_per_day(),
        },
        "activity_24h": {
            "registrations": counts_24h.get("agent_registered", 0),
            "directory_browse": counts_24h.get("agent_directory_browse", 0),
            "directory_search": counts_24h.get("agent_directory_search", 0),
            "directory_opt_ins": counts_24h.get("agent_directory_opt_in", 0),
            "auth_failures": counts_24h.get("agent_auth_failure", 0),
            "email_verifications": counts_24h.get("agent_email_verified", 0),
            "suspicious_events": audit.get("suspicious_24h", 0),
        },
        "suspicious_activity": {
            "events_24h": audit.get("suspicious_24h", 0),
            "recent": [
                e for e in audit.get("recent_events", []) if e.get("suspicious")
            ][:5],
        },
        "endpoints": {
            "register": "POST /agents/register",
            "profile": "GET /agents/me",
            "profile_update": "PATCH /agents/me",
            "rotate_key": "POST /agents/me/rotate-key",
            "directory": "GET /agents/directory",
            "recommended": "GET /agents/recommended",
            "onboarding_guide": "GET /agents/onboarding/guide",
            "terms": "GET /agents/terms",
        },
        "documentation": {
            "onboarding": "docs/agent-onboarding.md",
            "production_readiness": "docs/production-readiness-checklist.md",
            "terms": "docs/agent-terms.md",
            "terms_of_service": "docs/terms-of-service.md",
            "acceptable_use_policy": "docs/acceptable-use-policy.md",
        },
        "directory_prerequisites": [
            "terms_accepted (current version)",
            "email on file",
            "email verified (when ARCLYA_AGENT_REQUIRE_EMAIL_VERIFICATION=1)",
            "account status active",
        ],
    }


def build_public_platform_summary(
    root: Path,
    *,
    ops_status: str = "healthy",
    auth_enabled: bool = False,
    checked_at: str | None = None,
    payments: dict[str, Any] | None = None,
    component_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Visitor-friendly platform summary for /status and the HTML status page."""
    agents = build_agent_platform_status(root)
    accounts = agents["accounts"]
    components = component_health or build_component_health(root)
    pay = payments or {}
    activity = agents.get("activity_24h", {})
    return {
        "status": ops_status,
        "checked_at": checked_at,
        "public_url": resolve_public_base_url(),
        "public_url_source": public_url_source(),
        "auth_enabled": auth_enabled,
        "external_agents_status": agents["status"],
        "registration_open": True,
        "launch_ready": components.get("launch_ready", False),
        "onboarding_guide": "GET /agents/onboarding/guide",
        "agent_directory": "GET /agents/directory",
        "terms": "GET /agents/terms",
        "agent_card": "GET /.well-known/agent-card.json",
        "accounts_total": accounts.get("total", 0),
        "accounts_active": accounts.get("active", 0),
        "directory_listed": accounts.get("publicly_listed", 0),
        "email_verified": accounts.get("email_verified", 0),
        "terms_version": agents["terms_version"],
        "onboarding_guide_version": agents["onboarding_guide_version"],
        "activity_24h": activity,
        "suspicious_events_24h": activity.get("suspicious_events", 0),
        "payments": {
            "enabled": pay.get("enabled", False),
            "payment_count": pay.get("payment_count", 0),
            "pending_review_count": pay.get("pending_review_count", 0),
            "confirmed_total_usd": pay.get("confirmed_total_usd", 0),
            "activity_24h": (components.get("crypto") or {}).get("activity_24h", {}),
        },
        "components": {
            "email_status": (components.get("email") or {}).get("status"),
            "crypto_status": (components.get("crypto") or {}).get("status"),
            "email_delivery": (components.get("email") or {}).get("delivery_mode_effective"),
            "email_provider": (components.get("email") or {}).get("smtp_provider"),
        },
        "launch_next_steps": components.get("next_steps", []),
    }


def build_agent_platform_summary(root: Path) -> dict[str, Any]:
    """Compact external-agent summary for GET /health."""
    full = build_agent_platform_status(root)
    accounts = full["accounts"]
    components = build_component_health(root)
    return {
        "status": full["status"],
        "onboarding_guide_version": full["onboarding_guide_version"],
        "terms_version": full["terms_version"],
        "accounts_total": accounts.get("total", 0),
        "accounts_active": accounts.get("active", 0),
        "directory_listed": accounts.get("publicly_listed", 0),
        "email_verified": accounts.get("email_verified", 0),
        "email_verification_required": full["email_verification"]["required_for_directory"],
        "registrations_24h": full["activity_24h"]["registrations"],
        "suspicious_events_24h": full["activity_24h"].get("suspicious_events", 0),
        "public_url": resolve_public_base_url(),
        "launch_ready": components.get("launch_ready", False),
        "email_delivery": components.get("email", {}).get("delivery_mode_effective"),
    }