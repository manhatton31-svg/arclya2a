"""Terms of Service and Acceptable Use Policy for external agent accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CURRENT_TERMS_VERSION = "2026-07-01"
TERMS_DOC_PATH = "docs/agent-terms.md"
TERMS_OF_SERVICE_PATH = "docs/terms-of-service.md"
ACCEPTABLE_USE_POLICY_PATH = "docs/acceptable-use-policy.md"
TERMS_TITLE = "Arclya External Agent Terms of Service & Acceptable Use Policy"
TERMS_ACCEPT_FIELDS = ("terms_accepted", "accept_terms")


def current_terms_version() -> str:
    return CURRENT_TERMS_VERSION


def parse_terms_accepted(value: Any) -> bool | None:
    """Return True when the client explicitly accepts terms; False when explicitly declined; else None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return None


def validate_terms_acceptance_for_registration(value: Any) -> tuple[bool, str | None]:
    accepted = parse_terms_accepted(value)
    if accepted is True:
        return True, None
    return False, (
        "You must accept the current Terms of Service and Acceptable Use Policy "
        f"(version {CURRENT_TERMS_VERSION}). Send terms_accepted: true or accept_terms: true "
        "in the registration body."
    )


def has_accepted_current_terms(account: dict[str, Any]) -> bool:
    return (
        str(account.get("terms_version") or "") == CURRENT_TERMS_VERSION
        and bool(account.get("terms_accepted_at"))
    )


def apply_terms_acceptance(row: dict[str, Any], *, now: datetime | None = None) -> None:
    """Record acceptance of the current terms version on an account row."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    row["terms_version"] = CURRENT_TERMS_VERSION
    row["terms_accepted_at"] = ts


def build_terms_info(*, base_url: str | None = None) -> dict[str, Any]:
    base = base_url.rstrip("/") if base_url else None
    doc_url = f"{base}/{TERMS_DOC_PATH}" if base else f"/{TERMS_DOC_PATH}"
    tos_url = f"{base}/{TERMS_OF_SERVICE_PATH}" if base else f"/{TERMS_OF_SERVICE_PATH}"
    aup_url = f"{base}/{ACCEPTABLE_USE_POLICY_PATH}" if base else f"/{ACCEPTABLE_USE_POLICY_PATH}"
    return {
        "version": CURRENT_TERMS_VERSION,
        "title": TERMS_TITLE,
        "documentation": TERMS_DOC_PATH,
        "documentation_url": doc_url,
        "terms_of_service": TERMS_OF_SERVICE_PATH,
        "terms_of_service_url": tos_url,
        "acceptable_use_policy": ACCEPTABLE_USE_POLICY_PATH,
        "acceptable_use_policy_url": aup_url,
        "required_at_registration": True,
        "required_for_directory": True,
        "accept_field": "terms_accepted",
        "accept_field_aliases": ["accept_terms"],
        "accept_via_profile": "PATCH /agents/me with terms_accepted: true (or accept_terms: true)",
        "summary": (
            "External agents must accept the Arclya Terms of Service and Acceptable Use Policy "
            "before registration completes and before opting into the public Agent Directory."
        ),
    }