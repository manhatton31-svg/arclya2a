"""Structured audit logging for external agent account actions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AGENT_AUDIT_FILENAME = "agent_actions.jsonl"

EVENT_AGENT_REGISTERED = "agent_registered"
EVENT_PROFILE_UPDATED = "agent_profile_updated"
EVENT_DIRECTORY_OPT_IN = "agent_directory_opt_in"
EVENT_DIRECTORY_OPT_OUT = "agent_directory_opt_out"
EVENT_AUTH_FAILURE = "agent_auth_failure"
EVENT_DIRECTORY_SEARCH = "agent_directory_search"
EVENT_DIRECTORY_RECOMMENDATION = "agent_directory_recommendation"
EVENT_DIRECTORY_BROWSE = "agent_directory_browse"
EVENT_STATUS_CHANGED = "agent_status_changed"
EVENT_EMAIL_VERIFIED = "agent_email_verified"
EVENT_API_KEY_ROTATED = "agent_api_key_rotated"
EVENT_TERMS_ACCEPTED = "agent_terms_accepted"

DIRECTORY_EVENT_TYPES = frozenset({
    EVENT_DIRECTORY_SEARCH,
    EVENT_DIRECTORY_RECOMMENDATION,
    EVENT_DIRECTORY_BROWSE,
})

SUSPICIOUS_IP_DIRECTORY_WINDOW_MINUTES = 5
SUSPICIOUS_IP_DIRECTORY_THRESHOLD = 40
SUSPICIOUS_AGENT_RECOMMEND_WINDOW_MINUTES = 5
SUSPICIOUS_AGENT_RECOMMEND_THRESHOLD = 25

ALL_EVENT_TYPES = frozenset({
    EVENT_AGENT_REGISTERED,
    EVENT_PROFILE_UPDATED,
    EVENT_DIRECTORY_OPT_IN,
    EVENT_DIRECTORY_OPT_OUT,
    EVENT_AUTH_FAILURE,
    EVENT_DIRECTORY_SEARCH,
    EVENT_DIRECTORY_RECOMMENDATION,
    EVENT_DIRECTORY_BROWSE,
    EVENT_STATUS_CHANGED,
    EVENT_EMAIL_VERIFIED,
    EVENT_API_KEY_ROTATED,
    EVENT_TERMS_ACCEPTED,
})


def agent_audit_path(root: Path) -> Path:
    return root / "data" / "audit" / AGENT_AUDIT_FILENAME


def _ensure_audit_dir(root: Path) -> Path:
    audit_dir = root / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_all_events(root: Path) -> list[dict[str, Any]]:
    path = agent_audit_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _count_recent_events(
    root: Path,
    *,
    event_types: frozenset[str],
    client_ip: str | None = None,
    agent_id: str | None = None,
    window_minutes: int,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    count = 0
    for row in reversed(_load_all_events(root)):
        ts = _parse_timestamp(str(row.get("timestamp", "")))
        if ts is None or ts < cutoff:
            break
        if row.get("event_type") not in event_types:
            continue
        if client_ip and row.get("client_ip") != client_ip:
            continue
        if agent_id and row.get("agent_id") != agent_id:
            continue
        count += 1
    return count


def _detect_suspicious(
    root: Path,
    *,
    event_type: str,
    client_ip: str | None,
    agent_id: str | None,
) -> tuple[bool, str | None]:
    if event_type in {EVENT_DIRECTORY_SEARCH, EVENT_DIRECTORY_BROWSE} and client_ip:
        recent = _count_recent_events(
            root,
            event_types=DIRECTORY_EVENT_TYPES,
            client_ip=client_ip,
            window_minutes=SUSPICIOUS_IP_DIRECTORY_WINDOW_MINUTES,
        )
        if recent >= SUSPICIOUS_IP_DIRECTORY_THRESHOLD:
            return True, (
                f"High directory activity from IP ({recent + 1} events in "
                f"{SUSPICIOUS_IP_DIRECTORY_WINDOW_MINUTES}m)"
            )

    if event_type == EVENT_DIRECTORY_RECOMMENDATION and agent_id:
        recent = _count_recent_events(
            root,
            event_types=frozenset({EVENT_DIRECTORY_RECOMMENDATION}),
            agent_id=agent_id,
            window_minutes=SUSPICIOUS_AGENT_RECOMMEND_WINDOW_MINUTES,
        )
        if recent >= SUSPICIOUS_AGENT_RECOMMEND_THRESHOLD:
            return True, (
                f"High recommendation volume for agent ({recent + 1} events in "
                f"{SUSPICIOUS_AGENT_RECOMMEND_WINDOW_MINUTES}m)"
            )

    return False, None


def log_agent_audit(
    root: Path,
    *,
    event_type: str,
    agent_id: str | None = None,
    client_ip: str | None = None,
    path: str | None = None,
    method: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one agent audit event to data/audit/agent_actions.jsonl."""
    payload = dict(details or {})
    suspicious, suspicious_reason = _detect_suspicious(
        root,
        event_type=event_type,
        client_ip=client_ip,
        agent_id=agent_id,
    )
    if suspicious and suspicious_reason:
        payload["suspicious_reason"] = suspicious_reason

    record: dict[str, Any] = {
        "id": f"aae_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "agent_id": agent_id,
        "client_ip": client_ip,
        "path": path,
        "method": method,
        "details": payload,
        "suspicious": suspicious,
    }
    _ensure_audit_dir(root)
    audit_file = agent_audit_path(root)
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_agent_audit_events(
    root: Path,
    *,
    limit: int = 100,
    event_type: str | None = None,
    agent_id: str | None = None,
    suspicious_only: bool = False,
) -> list[dict[str, Any]]:
    """Return recent agent audit events, newest first."""
    limit = max(1, min(limit, 500))
    rows = _load_all_events(root)
    filtered: list[dict[str, Any]] = []
    for row in reversed(rows):
        if event_type and row.get("event_type") != event_type:
            continue
        if agent_id and row.get("agent_id") != agent_id:
            continue
        if suspicious_only and not row.get("suspicious"):
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def build_agent_audit_summary(root: Path, *, recent_limit: int = 15) -> dict[str, Any]:
    """Aggregate agent audit metrics for operators."""
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    counts_24h: dict[str, int] = {t: 0 for t in sorted(ALL_EVENT_TYPES)}
    suspicious_24h = 0
    total = 0

    for row in _load_all_events(root):
        total += 1
        ts = _parse_timestamp(str(row.get("timestamp", "")))
        if ts is None or ts < cutoff_24h:
            continue
        et = str(row.get("event_type", ""))
        if et in counts_24h:
            counts_24h[et] += 1
        if row.get("suspicious"):
            suspicious_24h += 1

    recent = read_agent_audit_events(root, limit=recent_limit)
    return {
        "audit_log": f"data/audit/{AGENT_AUDIT_FILENAME}",
        "total_events": total,
        "counts_24h": counts_24h,
        "suspicious_24h": suspicious_24h,
        "recent_events": recent,
        "operator_query": "GET /agents/audit (requires X-Arclya-Operator-Key)",
    }


def log_agent_registration(
    root: Path,
    *,
    account: dict[str, Any],
    client_ip: str | None,
    path: str = "/agents/register",
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_AGENT_REGISTERED,
        agent_id=account.get("agent_id"),
        client_ip=client_ip,
        path=path,
        method="POST",
        details={
            "agent_name": account.get("agent_name"),
            "capability_count": len(account.get("capabilities") or []),
            "has_email": bool(account.get("email")),
            "publicly_listed": bool(account.get("publicly_listed", False)),
        },
    )


def log_agent_profile_update(
    root: Path,
    *,
    account: dict[str, Any],
    changed_fields: list[str],
    client_ip: str | None,
    path: str = "/agents/me",
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_PROFILE_UPDATED,
        agent_id=account.get("agent_id"),
        client_ip=client_ip,
        path=path,
        method="PATCH",
        details={
            "agent_name": account.get("agent_name"),
            "changed_fields": changed_fields,
            "capability_count": len(account.get("capabilities") or []),
            "publicly_listed": bool(account.get("publicly_listed", False)),
        },
    )


def log_agent_directory_listing_change(
    root: Path,
    *,
    account: dict[str, Any],
    publicly_listed: bool,
    client_ip: str | None,
) -> dict[str, Any]:
    event_type = EVENT_DIRECTORY_OPT_IN if publicly_listed else EVENT_DIRECTORY_OPT_OUT
    return log_agent_audit(
        root,
        event_type=event_type,
        agent_id=account.get("agent_id"),
        client_ip=client_ip,
        path="/agents/me",
        method="PATCH",
        details={
            "agent_name": account.get("agent_name"),
            "publicly_listed": publicly_listed,
        },
    )


def log_agent_auth_failure(
    root: Path,
    request: Any,
    auth_err: dict[str, Any],
) -> dict[str, Any]:
    details = dict(auth_err.get("details") or {})
    details["status_code"] = auth_err.get("status_code", 401)
    details["message"] = auth_err.get("message")
    client_ip = request.client.host if getattr(request, "client", None) else None
    return log_agent_audit(
        root,
        event_type=EVENT_AUTH_FAILURE,
        agent_id=details.get("agent_id"),
        client_ip=client_ip,
        path=request.url.path,
        method=request.method,
        details=details,
    )


def log_agent_email_verified(
    root: Path,
    *,
    account: dict[str, Any],
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_EMAIL_VERIFIED,
        agent_id=account.get("agent_id"),
        path="/agents/verify-email",
        method="POST",
        details={
            "agent_name": account.get("agent_name"),
            "email_verified": True,
        },
    )


def log_agent_api_key_rotated(
    root: Path,
    *,
    account: dict[str, Any],
    rotated_by: str,
    revoked_key_prefixes: list[str] | None = None,
    operator_id: str | None = None,
    reason: str | None = None,
    path: str = "/agents/me/rotate-key",
    method: str = "POST",
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_API_KEY_ROTATED,
        agent_id=account.get("agent_id"),
        path=path,
        method=method,
        details={
            "agent_name": account.get("agent_name"),
            "rotated_by": rotated_by,
            "revoked_key_prefixes": revoked_key_prefixes or [],
            "new_key_prefix": account.get("api_key_prefix"),
            "operator_id": operator_id,
            "reason": reason,
        },
    )


def log_agent_terms_accepted(
    root: Path,
    *,
    account: dict[str, Any],
    path: str = "/agents/register",
    method: str = "POST",
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_TERMS_ACCEPTED,
        agent_id=account.get("agent_id"),
        path=path,
        method=method,
        details={
            "agent_name": account.get("agent_name"),
            "terms_version": account.get("terms_version"),
            "terms_accepted_at": account.get("terms_accepted_at"),
        },
    )


def log_agent_status_change(
    root: Path,
    *,
    account: dict[str, Any],
    previous_status: str,
    new_status: str,
    operator_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return log_agent_audit(
        root,
        event_type=EVENT_STATUS_CHANGED,
        agent_id=account.get("agent_id"),
        path=f"/agents/{account.get('agent_id')}/status",
        method="PATCH",
        details={
            "agent_name": account.get("agent_name"),
            "previous_status": previous_status,
            "new_status": new_status,
            "reason": reason,
            "operator_id": operator_id or "operator",
            "publicly_listed": bool(account.get("publicly_listed", False)),
        },
    )


def log_agent_directory_activity(
    root: Path,
    request: Any,
    *,
    mode: str,
    viewer_agent_id: str | None,
    filters: dict[str, Any],
    result_count: int,
    total: int,
) -> dict[str, Any]:
    client_ip = request.client.host if getattr(request, "client", None) else None
    if mode == "recommended":
        event_type = EVENT_DIRECTORY_RECOMMENDATION
    elif mode == "search":
        event_type = EVENT_DIRECTORY_SEARCH
    else:
        event_type = EVENT_DIRECTORY_BROWSE

    q = filters.get("q")
    q_preview = (str(q)[:80] + "…") if q and len(str(q)) > 80 else q

    return log_agent_audit(
        root,
        event_type=event_type,
        agent_id=viewer_agent_id,
        client_ip=client_ip,
        path=request.url.path,
        method="GET",
        details={
            "mode": mode,
            "filters": {
                "capabilities": filters.get("capabilities") or [],
                "q": q_preview,
                "recommended": bool(filters.get("recommended")),
            },
            "pagination": filters.get("pagination"),
            "result_count": result_count,
            "total_matches": total,
        },
    )