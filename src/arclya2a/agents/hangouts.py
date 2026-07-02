"""Topic-based collaboration hubs — searchable hangouts by capability or vertical."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.hangout_store import append_record, latest_by_id, load_records
from arclya2a.agents.security import is_valid_capability_token, sanitize_profile_text

HUBS_FILE = "collaboration_hubs.jsonl"
HUB_ID_PREFIX = "hub_"
MAX_HUBS_PER_AGENT = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_or_join_hub(
    root: Path,
    *,
    agent_id: str,
    topic: str,
    capability: str | None = None,
    vertical: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Create a collaboration hub or join an existing matching topic+capability."""
    clean_topic = sanitize_profile_text(topic, max_len=64)
    if not clean_topic:
        raise ValueError("topic is required")

    cap = None
    if capability:
        cap = str(capability).strip().lower()
        if not is_valid_capability_token(cap):
            raise ValueError("invalid capability token")

    existing = list_hubs(root, topic=clean_topic, capability=cap)
    for hub in existing:
        members = hub.get("member_agent_ids") or []
        if agent_id in members:
            return hub
        if len(members) < hub.get("max_members", 50):
            return _join_hub(root, hub["hub_id"], agent_id)

    agent_hubs = [h for h in list_hubs(root) if agent_id in (h.get("member_agent_ids") or [])]
    if len(agent_hubs) >= MAX_HUBS_PER_AGENT:
        raise ValueError(f"maximum {MAX_HUBS_PER_AGENT} hub memberships per agent")

    hub_id = f"{HUB_ID_PREFIX}{secrets.token_hex(6)}"
    record = {
        "hub_id": hub_id,
        "topic": clean_topic,
        "capability": cap,
        "vertical": sanitize_profile_text(vertical or "", max_len=64) or None,
        "description": sanitize_profile_text(description, max_len=500),
        "member_agent_ids": [agent_id],
        "max_members": 50,
        "created_at": _now(),
        "updated_at": _now(),
    }
    append_record(root, HUBS_FILE, record)
    return _public_hub(record)


def _join_hub(root: Path, hub_id: str, agent_id: str) -> dict[str, Any]:
    hubs = latest_by_id(load_records(root, HUBS_FILE), "hub_id")
    hub = hubs.get(hub_id)
    if not hub:
        raise ValueError("hub not found")
    members = list(hub.get("member_agent_ids") or [])
    if agent_id not in members:
        members.append(agent_id)
    hub["member_agent_ids"] = members
    hub["updated_at"] = _now()
    append_record(root, HUBS_FILE, hub)
    return _public_hub(hub)


def list_hubs(
    root: Path,
    *,
    topic: str | None = None,
    capability: str | None = None,
    vertical: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = list(latest_by_id(load_records(root, HUBS_FILE), "hub_id").values())
    if topic:
        t = topic.strip().lower()
        rows = [r for r in rows if t in str(r.get("topic", "")).lower()]
    if capability:
        c = capability.strip().lower()
        rows = [r for r in rows if r.get("capability") == c]
    if vertical:
        v = vertical.strip().lower()
        rows = [r for r in rows if v in str(r.get("vertical") or "").lower()]
    if q:
        needle = q.strip().lower()
        rows = [
            r
            for r in rows
            if needle in str(r.get("topic", "")).lower()
            or needle in str(r.get("description", "")).lower()
            or needle in str(r.get("vertical") or "").lower()
        ]
    rows.sort(key=lambda r: (r.get("updated_at", ""), -len(r.get("member_agent_ids") or [])), reverse=True)
    return [_public_hub(r) for r in rows[offset : offset + limit]]


def _public_hub(hub: dict[str, Any]) -> dict[str, Any]:
    members = hub.get("member_agent_ids") or []
    return {
        "hub_id": hub.get("hub_id"),
        "topic": hub.get("topic"),
        "capability": hub.get("capability"),
        "vertical": hub.get("vertical"),
        "description": hub.get("description", ""),
        "member_count": len(members),
        "member_agent_ids": members,
        "created_at": hub.get("created_at"),
        "updated_at": hub.get("updated_at"),
    }