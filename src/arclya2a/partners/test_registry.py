"""Lightweight registry of external test partners with security scoring."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.partners.sandbox import (
    MIN_GRADUATION_BEHAVIOR_SCORE,
    compute_behavior_score,
    evaluate_suspicious_behavior,
)

GRADUATION_CRITERIA: list[dict[str, str]] = [
    {"id": "profile_validated", "label": "Product profile passes POST /onboarding/validate"},
    {"id": "onboarding_complete", "label": "Handoff summary shows profile_saved / onboarding_complete"},
    {"id": "recruitment_reviewed", "label": "Recruiter ready_to_send outreach reviewed"},
    {"id": "close_dry_run", "label": "Warm close completes with lead_routing_confirmed in sandbox"},
    {
        "id": "no_emergency_stops",
        "label": "Zero EMERGENCY_STOP events in entire sandbox history",
    },
    {
        "id": "security_score_ok",
        "label": f"Behavior score ≥ {MIN_GRADUATION_BEHAVIOR_SCORE} with no active suspicious flags",
    },
]

SECURITY_GRADUATION_CRITERIA: list[dict[str, str]] = [
    {
        "id": "no_emergency_stops",
        "label": "No EMERGENCY_STOP in any sandbox handoff (emergency_stop_count must be 0)",
    },
    {
        "id": "security_score_ok",
        "label": f"Behavior score ≥ {MIN_GRADUATION_BEHAVIOR_SCORE}; no validation_abuse, burst_traffic, or high_risk_probe flags",
    },
    {
        "id": "no_blocked_actions",
        "label": "No attempts to invoke blocked high-risk tools or API paths",
    },
]


def _default_security() -> dict[str, Any]:
    return {
        "emergency_stop_count": 0,
        "failed_validation_count": 0,
        "blocked_action_count": 0,
        "rate_limit_hits": 0,
        "suspicious_flags": [],
        "behavior_score": 100,
        "last_emergency_stop_at": None,
    }


def _registry_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "registry.jsonl"


def register_test_partner(
    root: Path,
    *,
    agent_name: str,
    agent_card_url: str | None = None,
    target_customer: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    """Register a new test partner and return partner record."""
    partner_id = f"tp_{uuid.uuid4().hex[:12]}"
    entry = {
        "partner_id": partner_id,
        "agent_name": agent_name,
        "agent_card_url": agent_card_url,
        "target_customer": target_customer,
        "contact": contact,
        "status": "sandbox",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": None,
        "handoff_count": 0,
        "milestones": {c["id"]: False for c in GRADUATION_CRITERIA},
        "security": _default_security(),
        "graduation_ready": False,
    }
    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


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


def get_test_partner(root: Path, partner_id: str) -> dict[str, Any] | None:
    for row in _load_all(root):
        if row.get("partner_id") == partner_id:
            return row
    return None


def _refresh_security(row: dict[str, Any], root: Path) -> None:
    sec = row.setdefault("security", _default_security())
    eval_result = evaluate_suspicious_behavior(root, row["partner_id"], security=sec)
    sec["suspicious_flags"] = eval_result["suspicious_flags"]
    sec["behavior_score"] = compute_behavior_score(sec)

    milestones = row.setdefault("milestones", {})
    milestones["no_emergency_stops"] = int(sec.get("emergency_stop_count", 0)) == 0
    milestones["security_score_ok"] = (
        sec["behavior_score"] >= MIN_GRADUATION_BEHAVIOR_SCORE
        and len(sec["suspicious_flags"]) == 0
    )


def _update_graduation_status(row: dict[str, Any]) -> None:
    if row.get("status") == "graduated":
        row["graduation_ready"] = False
        return
    milestones = row.get("milestones") or {}
    row["graduation_ready"] = all(milestones.get(c["id"]) for c in GRADUATION_CRITERIA)
    if row["graduation_ready"]:
        row["status"] = "graduation_ready"
    elif row.get("status") == "graduation_ready":
        row["status"] = "sandbox"


def apply_security_event(
    root: Path,
    partner_id: str,
    *,
    event_type: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Apply a security event to partner metrics and recompute graduation eligibility."""
    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        if row.get("partner_id") != partner_id:
            continue
        sec = row.setdefault("security", _default_security())
        row["last_seen_at"] = now

        if event_type == "validation_failed":
            sec["failed_validation_count"] = int(sec.get("failed_validation_count", 0)) + 1
        elif event_type == "emergency_stop":
            sec["emergency_stop_count"] = int(sec.get("emergency_stop_count", 0)) + 1
            sec["last_emergency_stop_at"] = now
        elif event_type in ("blocked_tool", "blocked_path"):
            sec["blocked_action_count"] = int(sec.get("blocked_action_count", 0)) + 1
        elif event_type == "rate_limit_exceeded":
            sec["rate_limit_hits"] = int(sec.get("rate_limit_hits", 0)) + 1

        _refresh_security(row, root)
        _update_graduation_status(row)
        updated = row
        break

    if updated:
        _write_all(root, rows)
    return updated


def record_partner_activity(
    root: Path,
    partner_id: str,
    *,
    event: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update partner milestones and activity from sandbox events."""
    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        if row.get("partner_id") != partner_id:
            continue
        row["last_seen_at"] = now
        sec = row.setdefault("security", _default_security())

        if event == "handoff_complete":
            row["handoff_count"] = int(row.get("handoff_count", 0)) + 1
            summary = (details or {}).get("summary") or {}
            if summary.get("onboarding_complete") or summary.get("profile_saved"):
                row.setdefault("milestones", {})["onboarding_complete"] = True
            if summary.get("profile_saved"):
                row.setdefault("milestones", {})["profile_validated"] = True
            if summary.get("lead_routing_confirmed"):
                row.setdefault("milestones", {})["close_dry_run"] = True
            if summary.get("emergency_stop"):
                sec["emergency_stop_count"] = int(sec.get("emergency_stop_count", 0)) + 1
                sec["last_emergency_stop_at"] = now
        elif event == "profile_validated":
            row.setdefault("milestones", {})["profile_validated"] = True
        elif event == "recruitment_ready":
            row.setdefault("milestones", {})["recruitment_reviewed"] = True

        _refresh_security(row, root)
        _update_graduation_status(row)
        updated = row
        break

    if updated:
        _write_all(root, rows)
    return updated


def mark_partner_graduated(
    root: Path,
    partner_id: str,
    *,
    graduated_by: str,
    production_key_prefix: str,
) -> dict[str, Any] | None:
    """Set partner status to graduated after operator promotion."""
    rows = _load_all(root)
    updated: dict[str, Any] | None = None
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row.get("partner_id") != partner_id:
            continue
        row["status"] = "graduated"
        row["graduated_at"] = now
        row["graduated_by"] = graduated_by
        row["production_key_prefix"] = production_key_prefix
        row["graduation_ready"] = False
        updated = row
        break
    if updated:
        _write_all(root, rows)
    return updated


def list_test_partners(root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """List test partners, newest first (no API keys)."""
    rows = _load_all(root)
    rows.sort(key=lambda r: r.get("registered_at", ""), reverse=True)
    return [
        {
            "partner_id": r.get("partner_id"),
            "agent_name": r.get("agent_name"),
            "status": r.get("status"),
            "registered_at": r.get("registered_at"),
            "last_seen_at": r.get("last_seen_at"),
            "handoff_count": r.get("handoff_count", 0),
            "milestones": r.get("milestones", {}),
            "security": {
                "behavior_score": (r.get("security") or {}).get("behavior_score", 100),
                "emergency_stop_count": (r.get("security") or {}).get("emergency_stop_count", 0),
                "suspicious_flags": (r.get("security") or {}).get("suspicious_flags", []),
            },
            "graduation_ready": r.get("graduation_ready", False),
            "agent_card_url": r.get("agent_card_url"),
        }
        for r in rows[:limit]
    ]