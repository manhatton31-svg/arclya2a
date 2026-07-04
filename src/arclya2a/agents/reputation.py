"""Reputation and trust scoring for external agents (constitution-aligned)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import get_agent_account, is_active_agent_status, is_email_verified
from arclya2a.agents.hangout_store import load_records
from arclya2a.agents.terms import has_accepted_current_terms

REPUTATION_EVENT_FILE = "reputation_events.jsonl"


def record_reputation_event(
    root: Path,
    *,
    agent_id: str,
    event_type: str,
    delta: float,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a reputation signal (closes, verifications, moderation, spam flags)."""
    from arclya2a.agents.hangout_store import append_record

    record = {
        "agent_id": agent_id,
        "event_type": event_type,
        "delta": round(delta, 2),
        "source": source,
        "metadata": metadata or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    append_record(root, REPUTATION_EVENT_FILE, record)
    return record


def _deal_room_close_rows(root: Path, agent_id: str) -> list[dict[str, Any]]:
    from arclya2a.agents.deal_rooms import list_deal_rooms

    return [
        room
        for room in list_deal_rooms(root, agent_id=agent_id)
        if room.get("status") == "closed" and room.get("close_type") == "lead_routing_commitment"
    ]


def _deal_room_closes(root: Path, agent_id: str) -> int:
    return len(_deal_room_close_rows(root, agent_id))


def _constitutional_deal_room_closes(root: Path, agent_id: str) -> int:
    return sum(
        1
        for room in _deal_room_close_rows(root, agent_id)
        if (room.get("constitutional_verification") or {}).get("passed") is True
    )


def _marketplace_completion_rows(root: Path, agent_id: str) -> list[dict[str, Any]]:
    from arclya2a.agents.marketplace import list_marketplace_listings

    return [
        row
        for row in list_marketplace_listings(root, poster_id=agent_id, status="completed")
        if row.get("status") == "completed"
    ]


def _marketplace_completions(root: Path, agent_id: str) -> int:
    return len(_marketplace_completion_rows(root, agent_id))


def _constitutional_marketplace_completions(root: Path, agent_id: str) -> int:
    rows = _marketplace_completion_rows(root, agent_id)
    constitutional = 0
    for row in rows:
        verification = row.get("constitutional_verification")
        if verification is None and not (row.get("price_usd") or 0):
            constitutional += 1
        elif (verification or {}).get("passed") is True:
            constitutional += 1
    return constitutional


def _strictness_from_score(score: float, tier: str) -> dict[str, Any]:
    base_threshold = 85.0
    if score < 40:
        return {
            "level": "strict",
            "min_close_confidence": 92.0,
            "min_message_confidence": 75.0,
            "margin_multiplier": 1.15,
            "trust_score": score,
            "trust_tier": tier,
        }
    if score >= 80:
        return {
            "level": "trusted",
            "min_close_confidence": 82.0,
            "min_message_confidence": 65.0,
            "margin_multiplier": 1.0,
            "trust_score": score,
            "trust_tier": tier,
        }
    return {
        "level": "standard",
        "min_close_confidence": base_threshold,
        "min_message_confidence": 70.0,
        "margin_multiplier": 1.0,
        "trust_score": score,
        "trust_tier": tier,
    }


def _compute_trust_score_raw(root: Path, agent_id: str) -> dict[str, Any] | None:
    account = get_agent_account(root, agent_id)
    if not account:
        return None

    base = 40.0
    factors: dict[str, float] = {}

    if is_email_verified(account):
        factors["email_verified"] = 10.0
    if account.get("publicly_listed"):
        factors["directory_listed"] = 8.0
    if has_accepted_current_terms(account):
        factors["terms_current"] = 5.0
    if is_active_agent_status(account.get("status")):
        factors["active_status"] = 5.0

    closes = _deal_room_closes(root, agent_id)
    constitutional_closes = _constitutional_deal_room_closes(root, agent_id)
    if closes:
        constitutional_ratio = constitutional_closes / closes if closes else 0.0
        factors["deal_room_closes"] = min(20.0, constitutional_closes * 5.0)
        if constitutional_ratio < 1.0 and closes > constitutional_closes:
            factors["unguarded_close_penalty"] = max(-8.0, (constitutional_closes - closes) * 2.0)

    completions = _marketplace_completions(root, agent_id)
    constitutional_completions = _constitutional_marketplace_completions(root, agent_id)
    if completions:
        factors["marketplace_completions"] = min(12.0, constitutional_completions * 4.0)

    events = [e for e in load_records(root, REPUTATION_EVENT_FILE) if e.get("agent_id") == agent_id]
    event_bonus = sum(float(e.get("delta", 0)) for e in events if float(e.get("delta", 0)) > 0)
    event_penalty = sum(float(e.get("delta", 0)) for e in events if float(e.get("delta", 0)) < 0)
    if event_bonus:
        factors["reputation_events_bonus"] = min(10.0, event_bonus)
    if event_penalty:
        factors["reputation_events_penalty"] = max(-25.0, event_penalty)

    score = max(0.0, min(100.0, base + sum(factors.values())))
    tier = "trusted" if score >= 80 else "established" if score >= 60 else "building" if score >= 40 else "new"

    return {
        "trust_score": round(score, 1),
        "trust_tier": tier,
        "factors": factors,
        "deal_room_closes": closes,
        "constitutional_deal_room_closes": constitutional_closes,
        "marketplace_completions": completions,
        "constitutional_marketplace_completions": constitutional_completions,
        "constitutional_close_count": constitutional_closes + constitutional_completions,
    }


def guardrail_strictness(root: Path, agent_id: str | None = None) -> dict[str, Any]:
    """
    Reputation-informed guardrail strictness for handoffs and deal closes.

    Lower trust → higher required confidence; trusted agents get modest relief
    while remaining constitutional (never below profit_guardrail floor).
    """
    if not agent_id:
        return {
            "level": "standard",
            "min_close_confidence": 85.0,
            "min_message_confidence": 70.0,
            "margin_multiplier": 1.0,
        }
    raw = _compute_trust_score_raw(root, agent_id)
    if not raw:
        return {
            "level": "standard",
            "min_close_confidence": 85.0,
            "min_message_confidence": 70.0,
            "margin_multiplier": 1.0,
        }
    return _strictness_from_score(raw["trust_score"], raw["trust_tier"])


def compute_trust_score(root: Path, agent_id: str) -> dict[str, Any]:
    """
    Trust score 0–100 from verification, directory standing, closes, and penalties.

    Constitutional: rewards verified identity and successful lead-routing commitments;
    penalizes spam/suspicious audit signals.
    """
    raw = _compute_trust_score_raw(root, agent_id)
    if not raw:
        return {"agent_id": agent_id, "found": False}

    strictness = _strictness_from_score(raw["trust_score"], raw["trust_tier"])
    return {
        "agent_id": agent_id,
        "found": True,
        "trust_score": raw["trust_score"],
        "trust_tier": raw["trust_tier"],
        "factors": raw["factors"],
        "deal_room_closes": raw["deal_room_closes"],
        "constitutional_deal_room_closes": raw["constitutional_deal_room_closes"],
        "marketplace_completions": raw["marketplace_completions"],
        "constitutional_marketplace_completions": raw["constitutional_marketplace_completions"],
        "constitutional_close_count": raw["constitutional_close_count"],
        "guardrail_strictness": strictness,
        "directory_rank_boost": round(raw["trust_score"] / 100.0, 2),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def public_reputation_summary(root: Path, agent_id: str) -> dict[str, Any] | None:
    """Public-safe reputation block for profiles and directory."""
    result = compute_trust_score(root, agent_id)
    if not result.get("found"):
        return None
    return {
        "trust_score": result["trust_score"],
        "trust_tier": result["trust_tier"],
        "deal_room_closes": result["deal_room_closes"],
        "constitutional_deal_room_closes": result["constitutional_deal_room_closes"],
        "marketplace_completions": result["marketplace_completions"],
        "constitutional_marketplace_completions": result["constitutional_marketplace_completions"],
        "constitutional_close_count": result["constitutional_close_count"],
    }