"""Operator-level authentication for privileged partner management actions."""

from __future__ import annotations

import hmac
from fastapi import Request

from arclya2a.server.auth import extract_api_key
from arclya2a.settings import get_settings


def load_operator_key() -> str | None:
    """Load operator key from environment. Required for graduation actions."""
    return get_settings().arclya_operator_key


def extract_operator_key(request: Request) -> str | None:
    """Read operator key from X-Arclya-Operator-Key or X-Arclya-Key."""
    header = request.headers.get("X-Arclya-Operator-Key", "").strip()
    if header:
        return header
    return extract_api_key(request)


def verify_operator_key(request: Request, *, configured_key: str | None = None) -> bool:
    """Return True when request presents a valid operator key."""
    expected = configured_key if configured_key is not None else load_operator_key()
    if not expected:
        return False
    provided = extract_operator_key(request)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def verify_operator_key_value(provided: str | None, *, configured_key: str | None = None) -> bool:
    """Validate a raw operator key string (CLI use)."""
    expected = configured_key if configured_key is not None else load_operator_key()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.strip(), expected)