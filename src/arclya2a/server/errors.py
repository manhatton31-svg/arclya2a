"""Structured JSON error responses for the HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def error_body(
    *,
    code: str,
    message: str,
    details: Any = None,
    status_code: int = 400,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "status_code": status_code,
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def json_error(
    *,
    code: str,
    message: str,
    details: Any = None,
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code=code, message=message, details=details, status_code=status_code),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return json_error(
        code="internal_error",
        message="An unexpected error occurred",
        details=str(exc),
        status_code=500,
    )