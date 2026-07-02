"""Security, rate limiting, and abuse protection for external agent endpoints."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.security.injection_scanner import REJECT_CONFIDENCE, scan_text
from arclya2a.settings import get_settings

AGENT_KEY_PREFIX = "arclya_prod_"

DIRECTORY_DEFAULT_LIMIT = 50
DIRECTORY_MAX_LIMIT = 100
DIRECTORY_MAX_CAPABILITY_FILTERS = 10
DIRECTORY_SEARCH_MAX_LEN = 200
DIRECTORY_MAX_OFFSET = 10_000

_CAPABILITY_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXCESSIVE_WHITESPACE_RE = re.compile(r"[ \t]{3,}")

_RATE_LIMIT_BUCKETS = frozenset({"register", "directory", "recommended", "rotate_key"})


def agent_register_rate_limit_per_minute() -> int:
    return get_settings().agent_register_rate_limit_per_minute


def agent_directory_rate_limit_per_minute() -> int:
    return get_settings().agent_directory_rate_limit_per_minute


def agent_recommended_rate_limit_per_minute() -> int:
    return get_settings().agent_recommended_rate_limit_per_minute


def agent_rotate_key_rate_limit_per_minute() -> int:
    return get_settings().agent_rotate_key_rate_limit_per_minute


def agent_max_register_per_ip_per_day() -> int:
    return get_settings().agent_max_register_per_ip_per_day


def rate_limit_for_bucket(bucket: str) -> int:
    if bucket == "register":
        return agent_register_rate_limit_per_minute()
    if bucket == "recommended":
        return agent_recommended_rate_limit_per_minute()
    if bucket == "rotate_key":
        return agent_rotate_key_rate_limit_per_minute()
    return agent_directory_rate_limit_per_minute()


def resolve_agent_rate_limit_bucket(path: str, method: str) -> str | None:
    """Map request path/method to an agent rate-limit bucket, if any."""
    verb = method.upper()
    if path == "/agents/register" and verb == "POST":
        return "register"
    if path == "/agents/me/rotate-key" and verb == "POST":
        return "rotate_key"
    if verb != "GET":
        return None
    if path in ("/agents", "/agents/directory"):
        return "directory"
    if path == "/agents/recommended":
        return "recommended"
    return None


def _registration_log_path(root: Path) -> Path:
    return root / "data" / "agent_accounts" / "registration_log.jsonl"


def count_agent_registrations_today(root: Path, client_ip: str | None) -> int:
    if not client_ip:
        return 0
    path = _registration_log_path(root)
    if not path.exists():
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("client_ip") == client_ip and row.get("date") == today:
            count += 1
    return count


def log_agent_registration_attempt(
    root: Path,
    *,
    agent_name: str,
    client_ip: str | None,
    success: bool,
    agent_id: str | None = None,
    reason: str | None = None,
) -> None:
    path = _registration_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "agent_name": agent_name,
        "client_ip": client_ip,
        "success": success,
        "agent_id": agent_id,
        "reason": reason,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def check_agent_registration_allowed(
    root: Path,
    *,
    client_ip: str | None,
) -> tuple[bool, str | None]:
    """Daily IP cap for POST /agents/register."""
    if not client_ip:
        return True, None
    if count_agent_registrations_today(root, client_ip) >= agent_max_register_per_ip_per_day():
        return False, (
            f"Daily agent registration limit exceeded for this IP "
            f"(max {agent_max_register_per_ip_per_day()} per day)"
        )
    return True, None


def sanitize_profile_text(text: str | None) -> str:
    """Strip control characters and normalize whitespace in profile text fields."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHAR_RE.sub("", str(text))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _EXCESSIVE_WHITESPACE_RE.sub("  ", cleaned)
    return cleaned.strip()


def is_valid_capability_token(cap: str) -> bool:
    """Capability slugs: lowercase letters, digits, underscore, hyphen."""
    return bool(_CAPABILITY_TOKEN_RE.match(cap.strip().lower()))


def scan_profile_field(
    root: Path,
    text: str,
    *,
    field: str,
) -> tuple[bool, str | None]:
    """Reject profile text that matches high-confidence injection patterns."""
    if not text or not text.strip():
        return True, None
    hits = scan_text(text, root=root, source=field)
    if not hits:
        return True, None
    severities = sorted((float(h.get("severity", 0)) for h in hits), reverse=True)
    confidence = severities[0] + sum(0.05 for _ in severities[1:4])
    confidence = min(1.0, confidence)
    if confidence >= REJECT_CONFIDENCE:
        labels = ", ".join(h.get("label", h.get("id", "pattern")) for h in hits[:3])
        return False, (
            f"{field} contains disallowed content (detected: {labels}). "
            "Remove instruction overrides, role hijacks, or off-platform routing."
        )
    return True, None


def validate_directory_query(
    *,
    capabilities: list[str] | None,
    search: str | None,
    offset: int,
    limit: int,
    sort: str,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """
    Validate and normalize directory browse parameters.

    Returns (normalized, field_errors). normalized is None when validation fails.
    """
    issues: list[dict[str, str]] = []

    cap_list = capabilities or []
    if len(cap_list) > DIRECTORY_MAX_CAPABILITY_FILTERS:
        issues.append({
            "field": "capability",
            "message": (
                f"At most {DIRECTORY_MAX_CAPABILITY_FILTERS} capability filters "
                "allowed per request"
            ),
        })

    normalized_caps: list[str] = []
    seen: set[str] = set()
    for idx, raw in enumerate(cap_list):
        cap = sanitize_profile_text(str(raw or "")).lower()
        if not cap:
            issues.append({
                "field": "capability",
                "message": f"capability[{idx}] must be a non-empty string",
            })
            continue
        if len(cap) > 128:
            issues.append({
                "field": "capability",
                "message": f"capability[{idx}] must be at most 128 characters",
            })
            continue
        if not is_valid_capability_token(cap):
            issues.append({
                "field": "capability",
                "message": (
                    f"capability[{idx}] must use lowercase letters, digits, "
                    "underscores, or hyphens (e.g. lead_research)"
                ),
            })
            continue
        if cap not in seen:
            seen.add(cap)
            normalized_caps.append(cap)

    q = sanitize_profile_text(search) if search is not None else None
    if q and len(q) > DIRECTORY_SEARCH_MAX_LEN:
        issues.append({
            "field": "q",
            "message": f"q must be at most {DIRECTORY_SEARCH_MAX_LEN} characters",
        })

    if offset < 0:
        issues.append({"field": "offset", "message": "offset must be >= 0"})
    elif offset > DIRECTORY_MAX_OFFSET:
        issues.append({
            "field": "offset",
            "message": f"offset must be at most {DIRECTORY_MAX_OFFSET}",
        })

    if limit < 1:
        issues.append({"field": "limit", "message": "limit must be at least 1"})
    elif limit > DIRECTORY_MAX_LIMIT:
        issues.append({
            "field": "limit",
            "message": f"limit must be at most {DIRECTORY_MAX_LIMIT} per request",
        })

    if sort and len(sort) > 64:
        issues.append({"field": "sort", "message": "sort must be at most 64 characters"})

    if issues:
        return None, issues

    return {
        "capabilities": normalized_caps or None,
        "search": q or None,
        "offset": max(0, offset),
        "limit": max(1, min(limit, DIRECTORY_MAX_LIMIT)),
        "sort": sort,
    }, []


def build_agent_auth_error(
    request_path: str,
    *,
    provided_key: str | None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Structured authentication error details for agent account routes."""
    base_details: dict[str, Any] = {
        "register_url": "/agents/register",
        "profile_url": "/agents/me",
        "auth_headers": [
            "X-Arclya-Key: arclya_prod_<your_key>",
            "Authorization: Bearer arclya_prod_<your_key>",
        ],
    }

    if not provided_key:
        return {
            "code": "authentication_error",
            "message": (
                "Agent API key required. Register at POST /agents/register, "
                "then send your key on every request to /agents/me."
            ),
            "details": {
                **base_details,
                "reason": "missing_api_key",
                "hint": (
                    "Include X-Arclya-Key or Authorization: Bearer with the "
                    "arclya_prod_* key returned at registration (shown once)."
                ),
            },
            "status_code": 401,
        }

    key = provided_key.strip()
    if not key.startswith(AGENT_KEY_PREFIX):
        return {
            "code": "authentication_error",
            "message": "Invalid API key format for agent accounts",
            "details": {
                **base_details,
                "reason": "invalid_key_format",
                "expected_prefix": AGENT_KEY_PREFIX,
                "hint": (
                    "Agent account keys start with arclya_prod_. "
                    "Sandbox keys (arclya_sandbox_*) cannot access /agents/me."
                ),
            },
            "status_code": 401,
        }

    if root is not None:
        from arclya2a.agents.accounts import get_agent_account
        from arclya2a.partners.production_keys import lookup_production_key

        entry = lookup_production_key(root, key)
        if entry:
            partner_id = str(entry.get("partner_id") or "")
            if partner_id.startswith("ag_"):
                account = get_agent_account(root, partner_id)
                if account:
                    from arclya2a.agents.accounts import normalize_agent_status

                    acct_status = normalize_agent_status(account.get("status"))
                    if acct_status == "suspended":
                        return {
                            "code": "forbidden",
                            "message": "Agent account is suspended",
                            "details": {
                                **base_details,
                                "reason": "account_suspended",
                                "agent_id": partner_id,
                                "status_reason": account.get("status_reason"),
                                "hint": "Contact platform support to restore access.",
                            },
                            "status_code": 403,
                        }
                    if acct_status == "pending_review":
                        return {
                            "code": "forbidden",
                            "message": "Agent account is pending operator review",
                            "details": {
                                **base_details,
                                "reason": "account_pending_review",
                                "agent_id": partner_id,
                                "hint": "Your account is under review. Try again after approval.",
                            },
                            "status_code": 403,
                        }
            return {
                "code": "authentication_error",
                "message": "API key is not an agent account key",
                "details": {
                    **base_details,
                    "reason": "wrong_key_type",
                    "hint": (
                        "This key belongs to a partner account, not an external agent. "
                        "Register a dedicated agent at POST /agents/register."
                    ),
                },
                "status_code": 401,
            }

    return {
        "code": "authentication_error",
        "message": "Invalid or revoked agent API key",
        "details": {
            **base_details,
            "reason": "unknown_or_revoked_key",
            "key_prefix": key[:20] + "…" if len(key) > 20 else key[:12] + "…",
            "hint": (
                "The key was not found or was revoked after rotation. Keys are shown only once "
                "at registration or rotation. If compromised, rotate with POST /agents/me/rotate-key "
                "using your current key. If lost, contact the operator for a forced rotation."
            ),
        },
        "status_code": 401,
    }


def is_agent_account_path(path: str) -> bool:
    return path.startswith("/agents/me") or path == "/agents/recommended"