"""Structured agent feedback and feature-interest signals."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import sanitize_profile_text
from arclya2a.agents.preferences import (
    VALID_CLOSING_METHODS,
    account_preferences,
    build_preferences_summary,
)

FEEDBACK_FILENAME = "agent_feedback.jsonl"
LEARNING_SIGNALS_FILENAME = "agent_feedback_signals.jsonl"

VALID_CATEGORIES = frozenset({
    "feature_request",
    "closing_preference",
    "general",
    "bug_report",
})

VALID_FEATURE_INTERESTS = frozenset({
    "human_closing",
    "deal_rooms",
    "marketplace",
    "referrals",
    "crypto_payments",
    "other",
})

MESSAGE_MAX_LEN = 2000


def _feedback_path(root: Path) -> Path:
    return root / "data" / "agent_accounts" / FEEDBACK_FILENAME


def _learning_signals_path(root: Path) -> Path:
    return root / "learning" / LEARNING_SIGNALS_FILENAME


def _ensure_feedback_dir(root: Path) -> None:
    (root / "data" / "agent_accounts").mkdir(parents=True, exist_ok=True)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_feedback_input(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate POST /agents/feedback body."""
    category = str(body.get("category") or "general").strip().lower()
    if category not in VALID_CATEGORIES:
        return None, (
            f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
        )

    message = sanitize_profile_text(str(body.get("message") or ""))
    if not message:
        return None, "message is required"
    if len(message) > MESSAGE_MAX_LEN:
        return None, f"message must be at most {MESSAGE_MAX_LEN} characters"

    payload: dict[str, Any] = {
        "category": category,
        "message": message,
    }

    if "wants_human_closing" in body:
        value = body["wants_human_closing"]
        if not isinstance(value, bool):
            return None, "wants_human_closing must be a boolean"
        payload["wants_human_closing"] = value

    if "feature_interest" in body:
        feature = str(body.get("feature_interest") or "").strip().lower()
        if feature and feature not in VALID_FEATURE_INTERESTS:
            return None, (
                f"feature_interest must be one of: {', '.join(sorted(VALID_FEATURE_INTERESTS))}"
            )
        if feature:
            payload["feature_interest"] = feature

    if "preferred_closing_method" in body:
        method = str(body.get("preferred_closing_method") or "").strip().lower()
        if method not in VALID_CLOSING_METHODS:
            return None, (
                f"preferred_closing_method must be one of: {', '.join(sorted(VALID_CLOSING_METHODS))}"
            )
        payload["preferred_closing_method"] = method

    rating = body.get("rating")
    if rating is not None:
        if not isinstance(rating, int) or rating < 1 or rating > 5:
            return None, "rating must be an integer from 1 to 5"
        payload["rating"] = rating

    return payload, None


def submit_agent_feedback(
    root: Path,
    *,
    agent_id: str,
    agent_name: str | None = None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist feedback and emit a learning signal."""
    now = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "feedback_id": f"afb_{uuid.uuid4().hex[:12]}",
        "timestamp": now,
        "agent_id": agent_id,
        "agent_name": agent_name,
        **payload,
    }
    _ensure_feedback_dir(root)
    with open(_feedback_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    signal = emit_feedback_learning_signal(root, record)
    record["learning_signal_id"] = signal.get("signal_id")
    return record


def list_agent_feedback(
    root: Path,
    *,
    limit: int = 50,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    path = _feedback_path(root)
    if not path.exists():
        return []
    limit = max(1, min(limit, 200))
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if agent_id:
        rows = [r for r in rows if r.get("agent_id") == agent_id]
    return rows[-limit:]


def emit_feedback_learning_signal(root: Path, feedback: dict[str, Any]) -> dict[str, Any]:
    """Append agent feedback to the Meta Optimizer signal stream."""
    signal_id = f"afs_{uuid.uuid4().hex[:12]}"
    signal = {
        "signal_id": signal_id,
        "timestamp": feedback.get("timestamp"),
        "source": "agent_feedback",
        "agent_id": feedback.get("agent_id"),
        "category": feedback.get("category"),
        "feature_interest": feedback.get("feature_interest"),
        "wants_human_closing": feedback.get("wants_human_closing"),
        "preferred_closing_method": feedback.get("preferred_closing_method"),
        "rating": feedback.get("rating"),
        "message_preview": (feedback.get("message") or "")[:200],
        "issues_detected": _feedback_issues(feedback),
        "recommendations": _feedback_recommendations(feedback),
        "meta_optimizer_target": "prompts/closer_prompt.md",
        "priority": _feedback_priority(feedback),
    }
    learning_dir = root / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    with open(_learning_signals_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": signal["timestamp"], "improvement_signal": signal}) + "\n")
    return signal


def _feedback_issues(feedback: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if feedback.get("wants_human_closing") is True:
        issues.append("agent_wants_human_closing")
    if feedback.get("feature_interest") == "human_closing":
        issues.append("agent_interest_human_closing")
    if feedback.get("category") == "bug_report":
        issues.append("agent_bug_report")
    return issues


def _feedback_recommendations(feedback: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    if feedback.get("wants_human_closing") or feedback.get("feature_interest") == "human_closing":
        recs.append(
            "External agents requested human-assisted closing — evaluate hybrid close workflow "
            "and Closer prompt updates for human handoff option"
        )
    if feedback.get("preferred_closing_method") == "human_only":
        recs.append(
            "Agent prefers human_only closing — prioritize human-in-the-loop close path in roadmap"
        )
    if feedback.get("category") == "feature_request" and feedback.get("feature_interest"):
        recs.append(
            f"Feature request for {feedback['feature_interest']} — review product backlog"
        )
    return recs


def _feedback_priority(feedback: dict[str, Any]) -> str:
    if feedback.get("category") == "bug_report":
        return "high"
    if feedback.get("wants_human_closing") or feedback.get("feature_interest") == "human_closing":
        return "medium"
    return "low"


def analyze_agent_feedback(root: Path, *, hours: int = 168) -> dict[str, Any]:
    """Summarize agent feedback for Meta Optimizer and ops dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list_agent_feedback(root, limit=500)
    recent = [
        r for r in rows
        if (_parse_ts(str(r.get("timestamp", ""))) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]

    by_category: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    human_interest = 0
    issues: list[str] = []
    recommendations: list[str] = []

    for row in recent:
        cat = str(row.get("category", "general"))
        by_category[cat] = by_category.get(cat, 0) + 1
        feat = row.get("feature_interest")
        if feat:
            by_feature[str(feat)] = by_feature.get(str(feat), 0) + 1
        if row.get("wants_human_closing") or feat == "human_closing":
            human_interest += 1

    prefs = build_preferences_summary(root)
    if human_interest >= 2 or prefs.get("wants_human_closing_count", 0) >= 2:
        issues.append("agent_demand_human_closing")
        recommendations.append(
            f"{human_interest} recent feedback + {prefs.get('wants_human_closing_count', 0)} "
            "agents with wants_human_closing — prioritize hybrid/human close capabilities"
        )

    return {
        "total_feedback": len(rows),
        "recent_count": len(recent),
        "window_hours": hours,
        "by_category": by_category,
        "by_feature_interest": by_feature,
        "human_closing_interest_count": human_interest,
        "preferences_summary": prefs,
        "issues": issues,
        "recommendations": recommendations,
        "recent": recent[-10:],
    }


def build_feedback_ops_summary(root: Path) -> dict[str, Any]:
    """Compact feedback block for /ops/dashboard."""
    analysis = analyze_agent_feedback(root)
    return {
        "total_feedback": analysis["total_feedback"],
        "recent_7d": analysis["recent_count"],
        "human_closing_interest": analysis["human_closing_interest_count"],
        "by_category": analysis["by_category"],
        "by_feature_interest": analysis["by_feature_interest"],
        "preferences": analysis["preferences_summary"],
        "recent_feedback": analysis["recent"],
        "learning_signals_path": f"learning/{LEARNING_SIGNALS_FILENAME}",
    }