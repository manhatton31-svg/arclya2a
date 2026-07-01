"""API key authentication for external agent access."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

from fastapi import Request

from arclya2a.partners.production_keys import lookup_production_key
from arclya2a.partners.sandbox import lookup_sandbox_key
from arclya2a.settings import get_settings


def load_api_key() -> str | None:
    """Load production API key from environment. When unset, authentication is disabled."""
    return get_settings().arclya_api_key


def load_rate_limit_per_minute() -> int:
    return get_settings().rate_limit_per_minute


def extract_api_key(request: Request) -> str | None:
    """Read API key from X-Arclya-Key or Authorization: Bearer."""
    header_key = request.headers.get("X-Arclya-Key", "").strip()
    if header_key:
        return header_key

    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def extract_caller_id(request: Request) -> str | None:
    """Optional external agent identifier for audit logging."""
    caller = request.headers.get("X-Arclya-Agent-Id", "").strip()
    return caller or None


def verify_api_key(
    request: Request,
    configured_key: str | None,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """
    Validate the request API key when authentication is enabled.

    Supports production key (ARCLYA_API_KEY) and sandbox keys (arclya_sandbox_*).
    Returns caller context for logging. When auth is disabled, returns anonymous context
    unless a valid sandbox key is provided.
    """
    caller_agent = extract_caller_id(request)
    provided = extract_api_key(request)

    if root and provided:
        sandbox_entry = lookup_sandbox_key(root, provided)
        if sandbox_entry:
            return {
                "authenticated": True,
                "mode": "sandbox",
                "client_id": caller_agent or f"sandbox:{sandbox_entry.get('partner_id')}",
                "caller_agent": caller_agent,
                "partner_id": sandbox_entry.get("partner_id"),
                "agent_name": sandbox_entry.get("agent_name"),
                "key_prefix": provided[:20] + "…",
            }

        production_entry = lookup_production_key(root, provided)
        if production_entry:
            return {
                "authenticated": True,
                "mode": "production",
                "client_id": caller_agent or f"partner:{production_entry.get('partner_id')}",
                "caller_agent": caller_agent,
                "partner_id": production_entry.get("partner_id"),
                "agent_name": production_entry.get("agent_name"),
                "key_prefix": provided[:20] + "…",
                "graduated": True,
            }

    if not configured_key:
        client_id = caller_agent or request.client.host if request.client else "anonymous"
        return {
            "authenticated": False,
            "mode": "development",
            "client_id": client_id,
            "caller_agent": caller_agent,
        }

    if not provided or not hmac.compare_digest(provided, configured_key):
        return None

    key_prefix = provided[:6] + "…" if len(provided) > 6 else "key"
    client_id = caller_agent or f"key:{key_prefix}"
    return {
        "authenticated": True,
        "mode": "production",
        "client_id": client_id,
        "caller_agent": caller_agent,
        "key_prefix": key_prefix,
    }


PUBLIC_PATHS = frozenset({
    "/",
    "/health",
    "/status",
    "/ops/dashboard",
    "/onboarding/validate",
    "/partners/sandbox/register",
    "/partners/onboarding/guide",
    "/partners/test",
    "/.well-known/agent-card.json",
    "/tools",
    "/payments/crypto/networks",
})

PROTECTED_PREFIXES = (
    "/orchestrate/",
    "/learning/",
    "/prompt/",
    "/billing/",
    "/tools/executions",
    "/security/",
    "/partners/me/",
)


def path_requires_auth(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)