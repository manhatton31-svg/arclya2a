"""Structured error codes for tool execution."""

from __future__ import annotations

from typing import Any

# Validation / policy (no retry)
UNKNOWN_TOOL = "unknown_tool"
AGENT_NOT_ALLOWED = "agent_not_allowed"
CONNECTOR_UNAVAILABLE = "connector_unavailable"
MISSING_CONNECTOR = "missing_connector"
INVALID_PARAMETERS = "invalid_parameters"

# Execution (may retry if transient)
TRANSIENT_HTTP = "transient_http"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
PERMANENT_HTTP = "permanent_http"
CONNECTOR_ERROR = "connector_error"


def structured_error(
    *,
    error_code: str,
    message: str,
    transient: bool = False,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error_code": error_code,
        "error": message,
        "transient": transient,
        "error_detail": detail or {},
    }


def outcome_label(*, success: bool, skipped: bool = False, dry_run: bool = False) -> str:
    if dry_run and success:
        return "dry_run"
    if skipped:
        return "skipped"
    if success:
        return "success"
    return "failed"