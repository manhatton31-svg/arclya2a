"""External agent account registration and persistent identity."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.security import (
    DIRECTORY_MAX_LIMIT,
    is_valid_capability_token,
    sanitize_profile_text,
    scan_profile_field,
)
from arclya2a.partners.production_keys import (
    issue_production_key,
    lookup_production_key,
    rotate_production_key_for_partner,
)
from arclya2a.partners.sandbox import validate_agent_name

AGENT_ID_PREFIX = "ag_"
ACCOUNT_TYPE = "external_agent"
VALID_STATUSES = frozenset({"active", "suspended", "pending_review"})
LEGACY_STATUS_ALIASES = {"pending": "pending_review"}
DEFAULT_STATUS = "active"
OPERATOR_SETTABLE_STATUSES = frozenset({"active", "suspended", "pending_review"})
DEFAULT_DIRECTORY_SORT = "created_at_desc"
VALID_DIRECTORY_SORTS = frozenset({
    "created_at_desc",
    "created_at_asc",
    "agent_name_asc",
    "agent_name_desc",
    "relevance",
    "match_score",
})
DESCRIPTION_MAX_LEN = 2000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_AGENT_ID_RE = re.compile(r"^ag_[0-9a-f]{12}$")


def _registry_path(root: Path) -> Path:
    return root / "data" / "agent_accounts" / "accounts.jsonl"


def normalize_email(email: str | None) -> str | None:
    if email is None or not str(email).strip():
        return None
    return str(email).strip().lower()


def is_valid_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_RE.match((agent_id or "").strip()))


def normalize_agent_status(status: str | None) -> str:
    """Normalize agent account status (maps legacy `pending` → `pending_review`)."""
    raw = str(status or DEFAULT_STATUS).strip().lower()
    return LEGACY_STATUS_ALIASES.get(raw, raw)


def is_active_agent_status(status: str | None) -> bool:
    return normalize_agent_status(status) == DEFAULT_STATUS


def is_email_verified(account: dict[str, Any]) -> bool:
    return bool(account.get("email_verified"))


def directory_requires_email_verification() -> bool:
    from arclya2a.agents.email_verification import directory_requires_email_verification as _req

    return _req()


def has_accepted_current_terms(account: dict[str, Any]) -> bool:
    from arclya2a.agents.terms import has_accepted_current_terms as _accepted

    return _accepted(account)


def can_join_directory(account: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether an agent may set publicly_listed=true."""
    from arclya2a.agents.terms import current_terms_version

    missing: list[str] = []
    if not has_accepted_current_terms(account):
        missing.append(
            f"accept current Terms of Service (version {current_terms_version()}) "
            "via PATCH /agents/me with terms_accepted: true"
        )
    if not normalize_email(account.get("email")):
        missing.append("add an email address via PATCH /agents/me")
    if directory_requires_email_verification() and not is_email_verified(account):
        missing.append(
            "verify your email (check inbox, POST /agents/verify-email, "
            "or POST /agents/me/resend-verification)"
        )
    if not is_active_agent_status(account.get("status")):
        missing.append(
            "account must be active — contact the operator if suspended or pending review"
        )
    if missing:
        steps = "; ".join(f"{idx + 1}) {step}" for idx, step in enumerate(missing))
        return False, f"Directory opt-in requires: {steps}"
    return True, None


def is_directory_eligible(account: dict[str, Any]) -> bool:
    if not bool(account.get("publicly_listed")):
        return False
    if not is_active_agent_status(account.get("status")):
        return False
    if not has_accepted_current_terms(account):
        return False
    if directory_requires_email_verification() and not is_email_verified(account):
        return False
    return True


def validate_email(email: str | None) -> tuple[bool, str | None]:
    if email is None or not str(email).strip():
        return True, None
    raw = str(email).strip()
    if len(raw) > 254:
        return False, "email must be at most 254 characters"
    if not _EMAIL_RE.match(raw):
        return False, "email must be a valid address (e.g. agent@example.com)"
    return True, None


def validate_description(
    description: str | None,
    *,
    root: Path | None = None,
) -> tuple[bool, str | None]:
    if description is None:
        return True, None
    raw = sanitize_profile_text(description)
    if len(raw) > DESCRIPTION_MAX_LEN:
        return False, f"description must be at most {DESCRIPTION_MAX_LEN} characters"
    if root is not None and raw:
        ok, err = scan_profile_field(root, raw, field="description")
        if not ok:
            return False, err
    return True, None


def validate_capabilities(capabilities: Any) -> tuple[bool, str | None, list[str]]:
    if capabilities is None:
        return True, None, []
    if not isinstance(capabilities, list):
        return False, "capabilities must be a JSON array of strings (e.g. [\"recruitment\", \"closing\"])", []
    normalized: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(capabilities):
        if not isinstance(item, str) or not item.strip():
            return False, f"capabilities[{idx}] must be a non-empty string", []
        cap = sanitize_profile_text(item)
        if len(cap) > 128:
            return False, f"capabilities[{idx}] must be at most 128 characters", []
        if not is_valid_capability_token(cap):
            return False, (
                f"capabilities[{idx}] must use letters, digits, underscores, or hyphens "
                "(e.g. lead_research)"
            ), []
        key = cap.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(cap)
    if len(normalized) > 50:
        return False, "capabilities must contain at most 50 unique items", []
    return True, None, normalized


def find_account_by_email(root: Path, email: str) -> dict[str, Any] | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    for row in _load_all(root):
        row_email = normalize_email(row.get("email"))
        if row_email and row_email == normalized:
            return row
    return None


def field_errors(*errors: dict[str, str] | None) -> list[dict[str, str]]:
    return [e for e in errors if e]


def validate_registration_input(
    root: Path,
    *,
    agent_name: str,
    email: str | None = None,
    description: str | None = None,
    capabilities: Any = None,
    terms_accepted: Any = None,
    accept_terms: Any = None,
) -> list[dict[str, str]]:
    """Collect field-level validation errors for registration."""
    issues: list[dict[str, str]] = []

    name = (agent_name or "").strip()
    if not name:
        issues.append({
            "field": "agent_name",
            "message": "agent_name is required (2–128 characters, letters/numbers/spaces/hyphens)",
        })
    else:
        name_ok, name_err = validate_agent_name(name)
        if not name_ok:
            issues.append({"field": "agent_name", "message": name_err or "invalid agent_name"})

    email_ok, email_err = validate_email(email)
    if not email_ok:
        issues.append({"field": "email", "message": email_err or "invalid email"})
    elif email and find_account_by_email(root, email):
        issues.append({
            "field": "email",
            "message": "An account with this email already exists. Use a different email or omit it.",
        })

    desc_ok, desc_err = validate_description(description, root=root)
    if not desc_ok:
        issues.append({"field": "description", "message": desc_err or "invalid description"})

    caps_ok, caps_err, _ = validate_capabilities(capabilities)
    if not caps_ok:
        issues.append({"field": "capabilities", "message": caps_err or "invalid capabilities"})

    from arclya2a.agents.terms import validate_terms_acceptance_for_registration

    terms_value = terms_accepted if terms_accepted is not None else accept_terms
    terms_ok, terms_err = validate_terms_acceptance_for_registration(terms_value)
    if not terms_ok:
        issues.append({"field": "terms_accepted", "message": terms_err or "terms acceptance required"})

    return issues


def _load_all(root: Path) -> list[dict[str, Any]]:
    path = _registry_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_all(root: Path, rows: list[dict[str, Any]]) -> None:
    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def get_agent_account(root: Path, agent_id: str) -> dict[str, Any] | None:
    for row in _load_all(root):
        if row.get("agent_id") == agent_id:
            return row
    return None


def lookup_agent_by_api_key(root: Path, api_key: str) -> dict[str, Any] | None:
    entry = lookup_production_key(root, api_key)
    if not entry:
        return None
    agent_id = entry.get("partner_id") or ""
    if not str(agent_id).startswith(AGENT_ID_PREFIX):
        return None
    account = get_agent_account(root, str(agent_id))
    if not account:
        return None
    if not is_active_agent_status(account.get("status")):
        return None
    return account


def public_profile(account: dict[str, Any]) -> dict[str, Any]:
    """Compact public-safe profile summary (no API key or email)."""
    capabilities = account.get("capabilities", [])
    return {
        "agent_id": account.get("agent_id"),
        "agent_name": account.get("agent_name"),
        "description": account.get("description", ""),
        "capabilities": capabilities,
        "capability_count": len(capabilities),
        "status": account.get("status", DEFAULT_STATUS),
        "created_at": account.get("created_at"),
        "has_email": bool(account.get("email")),
        "email_verified": is_email_verified(account),
        "terms_version": account.get("terms_version"),
        "terms_accepted_at": account.get("terms_accepted_at"),
        "terms_accepted": has_accepted_current_terms(account),
        "publicly_listed": bool(account.get("publicly_listed", False)),
    }


def detailed_public_profile(
    account: dict[str, Any],
    *,
    profile_url: str | None = None,
) -> dict[str, Any]:
    """Rich public profile for GET /agents/{agent_id} (no email or API keys)."""
    capabilities = account.get("capabilities", [])
    profile = {
        "agent_id": account.get("agent_id"),
        "agent_name": account.get("agent_name"),
        "description": account.get("description", ""),
        "capabilities": capabilities,
        "capability_count": len(capabilities),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
        "status": account.get("status", DEFAULT_STATUS),
        "publicly_listed": bool(account.get("publicly_listed", False)),
        "has_email": bool(account.get("email")),
        "account_type": account.get("account_type", ACCOUNT_TYPE),
    }
    if profile_url:
        profile["profile_url"] = profile_url
    return profile


def directory_entry(
    account: dict[str, Any],
    *,
    relevance: float | None = None,
    match_score: float | None = None,
) -> dict[str, Any]:
    """Public directory listing entry."""
    capabilities = account.get("capabilities", [])
    entry: dict[str, Any] = {
        "agent_id": account.get("agent_id"),
        "agent_name": account.get("agent_name"),
        "description": account.get("description", ""),
        "capabilities": capabilities,
        "capability_count": len(capabilities),
        "created_at": account.get("created_at"),
        "publicly_listed": bool(account.get("publicly_listed", False)),
    }
    if relevance is not None:
        entry["relevance"] = round(relevance, 4)
    if match_score is not None:
        entry["match_score"] = round(match_score, 4)
    return entry


def normalize_capability_filters(capabilities: list[str] | str | None) -> list[str]:
    """Normalize capability filter values from query params."""
    if capabilities is None:
        return []
    if isinstance(capabilities, str):
        cap = capabilities.strip().lower()
        return [cap] if cap else []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in capabilities:
        cap = str(raw or "").strip().lower()
        if cap and cap not in seen:
            seen.add(cap)
            normalized.append(cap)
    return normalized


def _agent_capability_set(row: dict[str, Any]) -> set[str]:
    return {str(c).lower() for c in row.get("capabilities", []) if str(c).strip()}


def _agent_matches_capabilities(row: dict[str, Any], required: list[str]) -> bool:
    if not required:
        return True
    agent_caps = _agent_capability_set(row)
    return all(req in agent_caps for req in required)


def compute_search_relevance(row: dict[str, Any], query: str) -> float:
    """Score 0–1 for text search across name, description, and capabilities."""
    q = (query or "").strip().lower()
    if not q:
        return 0.0

    name = str(row.get("agent_name", "")).lower()
    desc = str(row.get("description", "")).lower()
    cap_list = [str(c).lower() for c in row.get("capabilities", [])]
    caps_text = " ".join(cap_list)

    score = 0.0

    if q in name:
        score += 10.0
    if q in desc:
        score += 5.0
    if q in caps_text or any(q in cap for cap in cap_list):
        score += 7.0

    tokens = [t for t in re.split(r"\s+", q) if len(t) >= 2]
    for token in tokens:
        if token in name:
            score += 3.0
        if any(token in cap or cap in token for cap in cap_list):
            score += 2.5
        if token in desc:
            score += 1.0

    denominator = max(10.0, len(tokens) * 6.5 + 5.0)
    return round(min(1.0, score / denominator), 4)


def compute_capability_match_score(
    row: dict[str, Any],
    viewer_capabilities: list[str],
) -> float:
    """Score 0–1 based on overlapping capabilities with the viewing agent."""
    viewer = {str(c).lower() for c in viewer_capabilities if str(c).strip()}
    if not viewer:
        return 0.0
    agent_caps = _agent_capability_set(row)
    shared = viewer & agent_caps
    if not shared:
        return 0.0
    union = viewer | agent_caps
    jaccard = len(shared) / len(union)
    coverage = len(shared) / len(viewer)
    return round(min(1.0, (coverage * 0.6) + (jaccard * 0.4)), 4)


def _sort_directory_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "relevance":
        return sorted(
            rows,
            key=lambda r: (
                float(r.get("_relevance", 0.0)),
                str(r.get("created_at", "")),
            ),
            reverse=True,
        )
    if sort == "match_score":
        return sorted(
            rows,
            key=lambda r: (
                float(r.get("_match_score", 0.0)),
                str(r.get("created_at", "")),
            ),
            reverse=True,
        )
    if sort == "created_at_asc":
        return sorted(rows, key=lambda r: str(r.get("created_at", "")))
    if sort == "agent_name_asc":
        return sorted(rows, key=lambda r: str(r.get("agent_name", "")).lower())
    if sort == "agent_name_desc":
        return sorted(
            rows,
            key=lambda r: str(r.get("agent_name", "")).lower(),
            reverse=True,
        )
    return sorted(rows, key=lambda r: str(r.get("created_at", "")), reverse=True)


def _resolve_directory_sort(
    sort: str,
    *,
    has_search: bool,
    recommended: bool,
) -> str:
    if sort == DEFAULT_DIRECTORY_SORT:
        if has_search:
            return "relevance"
        if recommended:
            return "match_score"
        return DEFAULT_DIRECTORY_SORT
    if sort in VALID_DIRECTORY_SORTS:
        if sort == "relevance" and not has_search:
            return DEFAULT_DIRECTORY_SORT
        if sort == "match_score" and not recommended:
            return DEFAULT_DIRECTORY_SORT
        return sort
    if has_search:
        return "relevance"
    if recommended:
        return "match_score"
    return DEFAULT_DIRECTORY_SORT


def private_profile(account: dict[str, Any]) -> dict[str, Any]:
    """Authenticated profile view for GET /agents/me."""
    from arclya2a.agents.email_verification import build_email_verification_status

    return {
        "agent_id": account.get("agent_id"),
        "agent_name": account.get("agent_name"),
        "email": account.get("email"),
        "description": account.get("description", ""),
        "capabilities": account.get("capabilities", []),
        "status": account.get("status", DEFAULT_STATUS),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
        "api_key_prefix": account.get("api_key_prefix"),
        "email_verified": is_email_verified(account),
        "email_verification": build_email_verification_status(account),
        "terms_version": account.get("terms_version"),
        "terms_accepted_at": account.get("terms_accepted_at"),
        "terms_accepted": has_accepted_current_terms(account),
        "publicly_listed": bool(account.get("publicly_listed", False)),
    }


def register_agent_account(
    root: Path,
    *,
    agent_name: str,
    email: str | None = None,
    description: str | None = None,
    capabilities: list[str] | None = None,
    terms_accepted: Any = None,
    accept_terms: Any = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """
    Register a new external agent account.

    Returns (account, api_key, error_message).
    """
    issues = validate_registration_input(
        root,
        agent_name=agent_name,
        email=email,
        description=description,
        capabilities=capabilities,
        terms_accepted=terms_accepted,
        accept_terms=accept_terms,
    )
    if issues:
        first = issues[0]
        return None, None, f"{first['field']}: {first['message']}"

    _, _, normalized_caps = validate_capabilities(capabilities)
    desc_ok, desc_err = validate_description(description, root=root)
    if not desc_ok:
        return None, None, desc_err

    agent_id = f"{AGENT_ID_PREFIX}{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    api_key = issue_production_key(
        root,
        partner_id=agent_id,
        agent_name=agent_name.strip(),
        graduated_by="agent_registration",
        metadata={"account_type": ACCOUNT_TYPE},
    )

    account = {
        "agent_id": agent_id,
        "agent_name": agent_name.strip(),
        "email": normalize_email(email),
        "description": sanitize_profile_text(description),
        "capabilities": normalized_caps,
        "status": DEFAULT_STATUS,
        "created_at": now,
        "updated_at": now,
        "api_key_prefix": api_key[:20] + "…",
        "account_type": ACCOUNT_TYPE,
        "publicly_listed": False,
        "email_verified": False,
        "email_verified_at": None,
        "terms_version": None,
        "terms_accepted_at": None,
    }
    from arclya2a.agents.terms import apply_terms_acceptance

    apply_terms_acceptance(account, now=datetime.fromisoformat(now.replace("Z", "+00:00")))

    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(account) + "\n")

    return account, api_key, None


def rotate_agent_api_key(
    root: Path,
    agent_id: str,
    *,
    current_key: str | None = None,
    rotated_by: str = "agent",
    operator_id: str | None = None,
    reason: str | None = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Rotate the production API key for an external agent account.

    Agent self-rotation requires current_key that matches the account.
    Operator rotation omits current_key and may target non-active accounts for recovery.

    Returns (new_api_key, updated_account, error_message).
    """
    account = get_agent_account(root, agent_id)
    if not account:
        return None, None, "Agent account not found"

    if rotated_by == "agent":
        row_status = normalize_agent_status(account.get("status"))
        if row_status == "suspended":
            return None, None, "Agent account is suspended"
        if row_status == "pending_review":
            return None, None, "Agent account is pending review"
        if not current_key:
            return None, None, "Current API key is required to rotate"
        entry = lookup_production_key(root, current_key)
        if not entry or entry.get("partner_id") != agent_id:
            return None, None, "Current API key does not match this agent account"

    new_key, revoked_prefixes = rotate_production_key_for_partner(
        root,
        partner_id=agent_id,
        agent_name=account.get("agent_name", "agent"),
        rotated_by=rotated_by,
        reason=reason or ("operator_rotation" if operator_id else "agent_rotation"),
        metadata={
            "rotation_reason": reason,
            "operator_id": operator_id,
        },
    )

    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row.get("agent_id") != agent_id:
            continue
        row["api_key_prefix"] = new_key[:20] + "…"
        row["updated_at"] = now
        row["last_key_rotated_at"] = now
        updated = row
        break

    if not updated:
        return None, None, "Agent account not found"

    _write_all(root, rows)
    updated["_revoked_key_prefixes"] = revoked_prefixes
    return new_key, updated, None


def update_agent_profile(
    root: Path,
    agent_id: str,
    *,
    agent_name: str | None = None,
    email: str | None = None,
    description: str | None = None,
    capabilities: list[str] | None = None,
    publicly_listed: bool | None = None,
    terms_accepted: bool | None = None,
    accept_terms: bool | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Update mutable profile fields. Returns (account, error_message)."""
    rows = _load_all(root)
    updated: dict[str, Any] | None = None

    for row in rows:
        if row.get("agent_id") != agent_id:
            continue
        row_status = normalize_agent_status(row.get("status"))
        if row_status == "suspended":
            return None, "Agent account is suspended"
        if row_status == "pending_review":
            return None, "Agent account is pending review"

        if agent_name is not None:
            name_ok, name_err = validate_agent_name(agent_name)
            if not name_ok:
                return None, name_err
            row["agent_name"] = agent_name.strip()

        if email is not None:
            email_ok, email_err = validate_email(email)
            if not email_ok:
                return None, email_err
            normalized = normalize_email(email)
            if normalized:
                existing = find_account_by_email(root, normalized)
                if existing and existing.get("agent_id") != agent_id:
                    return None, "An account with this email already exists"
            previous_email = normalize_email(row.get("email"))
            row["email"] = normalized
            if normalized != previous_email:
                row["email_verified"] = False
                row["email_verified_at"] = None
            elif not normalized:
                row["email_verified"] = False
                row["email_verified_at"] = None

        if description is not None:
            desc_ok, desc_err = validate_description(description, root=root)
            if not desc_ok:
                return None, desc_err
            row["description"] = sanitize_profile_text(description)

        if capabilities is not None:
            caps_ok, caps_err, normalized_caps = validate_capabilities(capabilities)
            if not caps_ok:
                return None, caps_err
            row["capabilities"] = normalized_caps

        terms_flag = terms_accepted if terms_accepted is not None else accept_terms
        if terms_flag is not None:
            if not isinstance(terms_flag, bool):
                return None, "terms_accepted must be a boolean (true to accept current terms)"
            if terms_flag is False:
                return None, (
                    "Terms acceptance cannot be revoked via API. "
                    "Contact support if you need to close your account."
                )
            from arclya2a.agents.terms import apply_terms_acceptance

            apply_terms_acceptance(row)

        if publicly_listed is not None:
            if not isinstance(publicly_listed, bool):
                return None, "publicly_listed must be a boolean (true or false)"
            if publicly_listed:
                ok, err = can_join_directory(row)
                if not ok:
                    return None, err
            row["publicly_listed"] = publicly_listed

        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        updated = row
        break

    if not updated:
        return None, "Agent account not found"

    _write_all(root, rows)
    return updated, None


def list_directory_agents(
    root: Path,
    *,
    capabilities: list[str] | str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = DEFAULT_DIRECTORY_SORT,
    recommended_for: dict[str, Any] | None = None,
    exclude_agent_id: str | None = None,
) -> dict[str, Any]:
    """Return paginated opted-in, active agents for the public directory."""
    cap_filters = normalize_capability_filters(capabilities)
    search_filter = (search or "").strip() or None
    has_search = bool(search_filter)
    recommended = recommended_for is not None
    offset = max(0, offset)
    limit = max(1, min(limit, DIRECTORY_MAX_LIMIT))
    sort_key = _resolve_directory_sort(sort, has_search=has_search, recommended=recommended)

    matched: list[dict[str, Any]] = []
    viewer_caps = list((recommended_for or {}).get("capabilities") or [])

    for row in _load_all(root):
        if not row.get("publicly_listed"):
            continue
        if not is_active_agent_status(row.get("status")):
            continue
        if directory_requires_email_verification() and not is_email_verified(row):
            continue
        if not has_accepted_current_terms(row):
            continue
        if exclude_agent_id and row.get("agent_id") == exclude_agent_id:
            continue

        if not _agent_matches_capabilities(row, cap_filters):
            continue

        enriched = dict(row)
        relevance = 0.0
        match_score = 0.0

        if search_filter:
            relevance = compute_search_relevance(row, search_filter)
            if relevance <= 0:
                continue
            enriched["_relevance"] = relevance

        if recommended:
            match_score = compute_capability_match_score(row, viewer_caps)
            if match_score <= 0:
                continue
            enriched["_match_score"] = match_score

        matched.append(enriched)

    sorted_rows = _sort_directory_rows(matched, sort_key)
    scoring_active = has_search or recommended
    page = [
        directory_entry(
            row,
            relevance=row.get("_relevance") if has_search else None,
            match_score=row.get("_match_score") if recommended else None,
        )
        for row in sorted_rows[offset : offset + limit]
    ]

    return {
        "total": len(sorted_rows),
        "agents": page,
        "offset": offset,
        "limit": limit,
        "sort": sort_key,
        "mode": "recommended" if recommended else ("search" if has_search else "browse"),
        "scoring_active": scoring_active,
        "capability_filters": cap_filters,
    }


def list_recommended_agents(
    root: Path,
    viewer: dict[str, Any],
    *,
    capabilities: list[str] | str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = "match_score",
) -> dict[str, Any]:
    """Agents recommended for an authenticated viewer based on capability overlap."""
    return list_directory_agents(
        root,
        capabilities=capabilities,
        search=search,
        offset=offset,
        limit=limit,
        sort=sort,
        recommended_for=viewer,
        exclude_agent_id=viewer.get("agent_id"),
    )


def set_agent_status(
    root: Path,
    agent_id: str,
    status: str,
    *,
    reason: str | None = None,
    operator_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Operator moderation: set agent status to active, suspended, or pending_review."""
    new_status = normalize_agent_status(status)
    if new_status not in OPERATOR_SETTABLE_STATUSES:
        return None, (
            f"status must be one of: {', '.join(sorted(OPERATOR_SETTABLE_STATUSES))}"
        )

    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    previous_status: str | None = None

    for row in rows:
        if row.get("agent_id") != agent_id:
            continue
        previous_status = normalize_agent_status(row.get("status"))
        row["status"] = new_status
        row["status_reason"] = (reason or "").strip() or None
        row["status_changed_at"] = datetime.now(timezone.utc).isoformat()
        row["status_changed_by"] = operator_id or "operator"
        row["updated_at"] = row["status_changed_at"]
        if new_status in {"suspended", "pending_review"}:
            row["publicly_listed"] = False
        updated = row
        break

    if not updated:
        return None, "Agent account not found"

    _write_all(root, rows)
    return updated, None


def count_agent_accounts(root: Path) -> dict[str, int]:
    rows = _load_all(root)
    counts = {
        "total": len(rows),
        "active": 0,
        "suspended": 0,
        "pending_review": 0,
        "publicly_listed": 0,
        "email_verified": 0,
    }
    for row in rows:
        status = normalize_agent_status(row.get("status", DEFAULT_STATUS))
        if status in counts:
            counts[status] += 1
        if is_email_verified(row):
            counts["email_verified"] += 1
        if is_directory_eligible(row):
            counts["publicly_listed"] += 1
    return counts