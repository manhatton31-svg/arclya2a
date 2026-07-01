"""API key authentication for external agent access."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Request


def load_api_key() -> str | None:
    """Load API key from environment. When unset, authentication is disabled."""
    key = os.environ.get("ARCLYA_API_KEY", "").strip()
    return key or None


def load_rate_limit_per_minute() -> int:
    raw = os.environ.get("ARCLYA_RATE_LIMIT_PER_MINUTE", "60").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 60
    return max(1, value)


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


def verify_api_key(request: Request, configured_key: str | None) -> dict[str, Any] | None:
    """
    Validate the request API key when authentication is enabled.

    Returns caller context for logging. When auth is disabled, returns anonymous context.
    """
    caller_agent = extract_caller_id(request)
    if not configured_key:
        client_id = caller_agent or request.client.host if request.client else "anonymous"
        return {"authenticated": False, "client_id": client_id, "caller_agent": caller_agent}

    provided = extract_api_key(request)
    if not provided or not hmac.compare_digest(provided, configured_key):
        return None  # caller must treat None as auth failure

    # Log only a short non-reversible prefix — never the full secret.
    key_prefix = provided[:6] + "…" if len(provided) > 6 else "key"
    client_id = caller_agent or f"key:{key_prefix}"
    return {
        "authenticated": True,
        "client_id": client_id,
        "caller_agent": caller_agent,
        "key_prefix": key_prefix,
    }


PUBLIC_PATHS = frozenset({"/health", "/.well-known/agent-card.json"})

PROTECTED_PREFIXES = (
    "/orchestrate/",
    "/learning/",
    "/prompt/",
)


def path_requires_auth(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)