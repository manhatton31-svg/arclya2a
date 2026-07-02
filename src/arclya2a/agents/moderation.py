"""Operator management and moderation for external agent accounts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import (
    DEFAULT_STATUS,
    get_agent_account,
    normalize_agent_status,
)
from arclya2a.agents.audit import _load_all_events, _parse_timestamp

OPERATOR_LIST_DEFAULT_LIMIT = 50
OPERATOR_LIST_MAX_LIMIT = 200
OPERATOR_SORTS = frozenset({
    "created_at_desc",
    "created_at_asc",
    "updated_at_desc",
    "agent_name_asc",
    "agent_name_desc",
})
RECENT_ACTIVITY_DAYS = 7


def _recent_audit_index(root: Path, *, days: int = RECENT_ACTIVITY_DAYS) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    index: dict[str, dict[str, Any]] = {}
    for row in _load_all_events(root):
        agent_id = row.get("agent_id")
        if not agent_id:
            continue
        ts = _parse_timestamp(str(row.get("timestamp", "")))
        if ts is None or ts < cutoff:
            continue
        entry = index.setdefault(
            str(agent_id),
            {"event_count": 0, "last_audit_at": None},
        )
        entry["event_count"] += 1
        stamp = row.get("timestamp")
        if entry["last_audit_at"] is None or str(stamp) > str(entry["last_audit_at"]):
            entry["last_audit_at"] = stamp
    return index


def operator_agent_entry(
    account: dict[str, Any],
    *,
    audit_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Operator-safe agent summary (no email, no API key)."""
    stats = audit_stats or {}
    capabilities = account.get("capabilities", [])
    status = normalize_agent_status(str(account.get("status", DEFAULT_STATUS)))
    return {
        "agent_id": account.get("agent_id"),
        "agent_name": account.get("agent_name"),
        "status": status,
        "publicly_listed": bool(account.get("publicly_listed", False)),
        "capability_count": len(capabilities),
        "capabilities": capabilities,
        "description": account.get("description", ""),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
        "has_email": bool(account.get("email")),
        "status_reason": account.get("status_reason"),
        "status_changed_at": account.get("status_changed_at"),
        "status_changed_by": account.get("status_changed_by"),
        "recent_event_count_7d": int(stats.get("event_count", 0)),
        "last_audit_at": stats.get("last_audit_at"),
    }


def list_agent_accounts_for_operator(
    root: Path,
    *,
    status: str | None = None,
    publicly_listed: bool | None = None,
    q: str | None = None,
    recently_active: bool = False,
    offset: int = 0,
    limit: int = OPERATOR_LIST_DEFAULT_LIMIT,
    sort: str = "created_at_desc",
) -> dict[str, Any]:
    """Paginated operator view of all external agent accounts."""
    from arclya2a.agents.accounts import _load_all

    offset = max(0, offset)
    limit = max(1, min(limit, OPERATOR_LIST_MAX_LIMIT))
    sort_key = sort if sort in OPERATOR_SORTS else "created_at_desc"
    status_filter = normalize_agent_status(status) if status else None
    search = (q or "").strip().lower() or None
    audit_index = _recent_audit_index(root)

    matched: list[dict[str, Any]] = []
    for row in _load_all(root):
        row_status = normalize_agent_status(str(row.get("status", DEFAULT_STATUS)))
        if status_filter and row_status != status_filter:
            continue
        if publicly_listed is not None and bool(row.get("publicly_listed")) != publicly_listed:
            continue
        if search:
            name = str(row.get("agent_name", "")).lower()
            agent_id = str(row.get("agent_id", "")).lower()
            if search not in name and search not in agent_id:
                continue
        agent_id = str(row.get("agent_id", ""))
        stats = audit_index.get(agent_id, {})
        if recently_active and not stats.get("event_count"):
            continue
        matched.append(operator_agent_entry(row, audit_stats=stats))

    if sort_key == "created_at_asc":
        matched.sort(key=lambda r: str(r.get("created_at", "")))
    elif sort_key == "updated_at_desc":
        matched.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    elif sort_key == "agent_name_asc":
        matched.sort(key=lambda r: str(r.get("agent_name", "")).lower())
    elif sort_key == "agent_name_desc":
        matched.sort(key=lambda r: str(r.get("agent_name", "")).lower(), reverse=True)
    else:
        matched.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)

    page = matched[offset : offset + limit]
    return {
        "total": len(matched),
        "count": len(page),
        "agents": page,
        "offset": offset,
        "limit": limit,
        "sort": sort_key,
        "filters": {
            "status": status_filter,
            "publicly_listed": publicly_listed,
            "q": q,
            "recently_active": recently_active,
        },
    }


def build_agent_management_summary(root: Path) -> dict[str, Any]:
    """Management metrics for ops dashboard External Agents section."""
    from arclya2a.agents.accounts import _load_all, count_agent_accounts

    counts = count_agent_accounts(root)
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    recently_registered: list[dict[str, Any]] = []
    recently_suspended: list[dict[str, Any]] = []

    for row in _load_all(root):
        created = _parse_timestamp(str(row.get("created_at", "")))
        if created and created >= cutoff_7d:
            recently_registered.append({
                "agent_id": row.get("agent_id"),
                "agent_name": row.get("agent_name"),
                "created_at": row.get("created_at"),
                "status": normalize_agent_status(str(row.get("status", DEFAULT_STATUS))),
            })
        status_changed = _parse_timestamp(str(row.get("status_changed_at", "")))
        if (
            normalize_agent_status(str(row.get("status", ""))) == "suspended"
            and status_changed
            and status_changed >= cutoff_7d
        ):
            recently_suspended.append({
                "agent_id": row.get("agent_id"),
                "agent_name": row.get("agent_name"),
                "status_changed_at": row.get("status_changed_at"),
                "status_reason": row.get("status_reason"),
            })

    recently_registered.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    recently_suspended.sort(key=lambda r: str(r.get("status_changed_at", "")), reverse=True)

    return {
        "total_agents": counts.get("total", 0),
        "active": counts.get("active", 0),
        "suspended": counts.get("suspended", 0),
        "pending_review": counts.get("pending_review", 0),
        "publicly_listed": counts.get("publicly_listed", 0),
        "registered_last_7d": len(recently_registered),
        "suspended_last_7d": len(recently_suspended),
        "recently_registered": recently_registered[:10],
        "recently_suspended": recently_suspended[:10],
        "operator_endpoints": {
            "list": "GET /agents/manage",
            "set_status": "PATCH /agents/{agent_id}/status",
            "agent_audit": "GET /agents/{agent_id}/audit",
            "global_audit": "GET /agents/audit",
        },
        "valid_statuses": ["active", "suspended", "pending_review"],
    }