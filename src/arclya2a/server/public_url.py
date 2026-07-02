"""Resolve public base URLs for HTTP responses (custom domain aware)."""

from __future__ import annotations

from fastapi import Request

from arclya2a.settings import resolve_public_base_url


def resolve_request_public_url(request: Request) -> str:
    """Prefer ARCLYA_PUBLIC_URL / RENDER_EXTERNAL_URL over the incoming request host."""
    return resolve_public_base_url(fallback=str(request.base_url).rstrip("/"))