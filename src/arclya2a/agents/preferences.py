"""Agent feature preferences (closing method, human assist, etc.)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

VALID_CLOSING_METHODS = frozenset({"agent_only", "human_only", "hybrid"})

DEFAULT_PREFERENCES: dict[str, Any] = {
    "wants_human_closing": False,
    "preferred_closing_method": "agent_only",
}


def default_preferences() -> dict[str, Any]:
    return dict(DEFAULT_PREFERENCES)


def normalize_preferences(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge stored preferences with defaults."""
    base = default_preferences()
    if not isinstance(raw, dict):
        return base
    if "wants_human_closing" in raw:
        base["wants_human_closing"] = bool(raw["wants_human_closing"])
    method = str(raw.get("preferred_closing_method") or "agent_only").strip().lower()
    if method in VALID_CLOSING_METHODS:
        base["preferred_closing_method"] = method
    return base


def account_preferences(account: dict[str, Any]) -> dict[str, Any]:
    return normalize_preferences(account.get("preferences"))


def validate_preferences_patch(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate PATCH /agents/me/preferences body. Returns (patch, error)."""
    if not body:
        return None, "At least one preference field is required"

    patch: dict[str, Any] = {}
    allowed = {"wants_human_closing", "preferred_closing_method"}
    unknown = [k for k in body if k not in allowed]
    if unknown:
        return None, f"Unknown preference fields: {', '.join(unknown)}"

    if "wants_human_closing" in body:
        value = body["wants_human_closing"]
        if not isinstance(value, bool):
            return None, "wants_human_closing must be a boolean"
        patch["wants_human_closing"] = value

    if "preferred_closing_method" in body:
        method = str(body["preferred_closing_method"] or "").strip().lower()
        if method not in VALID_CLOSING_METHODS:
            return None, (
                f"preferred_closing_method must be one of: "
                f"{', '.join(sorted(VALID_CLOSING_METHODS))}"
            )
        patch["preferred_closing_method"] = method

    if not patch:
        return None, "At least one preference field is required"
    return patch, None


def update_agent_preferences(
    root,
    agent_id: str,
    *,
    wants_human_closing: bool | None = None,
    preferred_closing_method: str | None = None,
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """
    Update agent preferences. Returns (account, changed_fields, error).
    """
    from arclya2a.agents.accounts import _load_all, _write_all, normalize_agent_status

    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    changed: list[str] = []

    for row in rows:
        if row.get("agent_id") != agent_id:
            continue
        row_status = normalize_agent_status(row.get("status"))
        if row_status == "suspended":
            return None, [], "Agent account is suspended"
        if row_status == "pending_review":
            return None, [], "Agent account is pending review"

        prefs = normalize_preferences(row.get("preferences"))
        if wants_human_closing is not None:
            prefs["wants_human_closing"] = wants_human_closing
            changed.append("wants_human_closing")
        if preferred_closing_method is not None:
            prefs["preferred_closing_method"] = preferred_closing_method
            changed.append("preferred_closing_method")

        if wants_human_closing is True and preferred_closing_method is None:
            if prefs["preferred_closing_method"] == "agent_only":
                prefs["preferred_closing_method"] = "hybrid"
                if "preferred_closing_method" not in changed:
                    changed.append("preferred_closing_method")

        row["preferences"] = prefs
        row["preferences_updated_at"] = datetime.now(timezone.utc).isoformat()
        row["updated_at"] = row["preferences_updated_at"]
        updated = row
        break

    if not updated:
        return None, [], "Agent account not found"

    _write_all(root, rows)
    return updated, changed, None


def build_preferences_summary(root) -> dict[str, Any]:
    """Aggregate preference signals for operators and learning."""
    from arclya2a.agents.accounts import _load_all

    rows = _load_all(root)
    by_method: dict[str, int] = {m: 0 for m in sorted(VALID_CLOSING_METHODS)}
    wants_human = 0
    total_with_prefs = 0

    for row in rows:
        prefs = normalize_preferences(row.get("preferences"))
        if row.get("preferences") or prefs != default_preferences():
            total_with_prefs += 1
        by_method[prefs["preferred_closing_method"]] = (
            by_method.get(prefs["preferred_closing_method"], 0) + 1
        )
        if prefs["wants_human_closing"]:
            wants_human += 1

    return {
        "total_agents": len(rows),
        "agents_with_explicit_preferences": total_with_prefs,
        "wants_human_closing_count": wants_human,
        "wants_human_closing_rate": round(wants_human / len(rows), 4) if rows else 0.0,
        "by_preferred_closing_method": by_method,
    }