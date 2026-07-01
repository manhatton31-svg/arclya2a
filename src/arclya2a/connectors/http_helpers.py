"""HTTP error classification for connector retry logic."""

from __future__ import annotations

import httpx

from arclya2a.tools.errors import (
    NETWORK_ERROR,
    PERMANENT_HTTP,
    RATE_LIMITED,
    TIMEOUT,
    TRANSIENT_HTTP,
)


def classify_http_status(status_code: int) -> tuple[str, bool]:
    """Return (error_code, is_transient)."""
    if status_code == 429:
        return RATE_LIMITED, True
    if status_code in (408, 500, 502, 503, 504):
        return TRANSIENT_HTTP, True
    if status_code >= 500:
        return TRANSIENT_HTTP, True
    return PERMANENT_HTTP, False


def classify_http_error(exc: httpx.HTTPError) -> tuple[str, bool, str]:
    """Return (error_code, is_transient, message)."""
    if isinstance(exc, httpx.TimeoutException):
        return TIMEOUT, True, f"Request timed out: {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        code, transient = classify_http_status(exc.response.status_code)
        return code, transient, f"HTTP {exc.response.status_code}: {exc}"
    return NETWORK_ERROR, True, str(exc)