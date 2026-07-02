"""Persistent agent-to-agent Deal Rooms for negotiation and lead-routing closes."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from arclya2a.agents.hangout_store import append_record, latest_by_id, load_records
from arclya2a.agents.security import sanitize_profile_text, scan_profile_field

DEAL_ROOMS_FILE = "deal_rooms.jsonl"
ROOM_ID_PREFIX = "dr_"
MAX_PARTICIPANTS = 4
MAX_MESSAGES = 100
MAX_TOPIC_LEN = 128
MAX_MESSAGE_LEN = 4000
_VALID_CLOSE_TYPES = frozenset({"lead_routing_commitment", "service_agreement", "exploratory"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_participants(participants: list[str]) -> list[str]:
    seen: list[str] = []
    for pid in participants:
        p = str(pid).strip()
        if p and p not in seen:
            seen.append(p)
    return seen[:MAX_PARTICIPANTS]


def create_deal_room(
    root: Path,
    *,
    host_agent_id: str,
    title: str,
    topic: str,
    capabilities: list[str] | None = None,
    invite_agent_ids: list[str] | None = None,
    handoff_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open a constitutional deal room (strong handoff, margin-positive closes)."""
    clean_title = sanitize_profile_text(title, max_len=200)
    if not clean_title:
        raise ValueError("title is required")
    clean_topic = sanitize_profile_text(topic, max_len=MAX_TOPIC_LEN) or "general"
    ok, err = scan_profile_field(root, clean_title + " " + clean_topic, field="deal_room")
    if not ok:
        raise ValueError(err or "title or topic failed security scan")

    room_id = f"{ROOM_ID_PREFIX}{secrets.token_hex(8)}"
    participants = _normalize_participants([host_agent_id, *(invite_agent_ids or [])])
    record = {
        "room_id": room_id,
        "title": clean_title,
        "topic": clean_topic,
        "capabilities": capabilities or [],
        "host_agent_id": host_agent_id,
        "participants": participants,
        "status": "open",
        "messages": [],
        "handoff_context": handoff_context or {},
        "constitutional": {
            "inference": "xai_only",
            "margin_guardrail": "profit_guardrail",
            "qc_gate": "final_arbiter",
            "close_type_default": "lead_routing_commitment",
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    append_record(root, DEAL_ROOMS_FILE, record)
    return record


def list_deal_rooms(
    root: Path,
    *,
    agent_id: str | None = None,
    topic: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = list(latest_by_id(load_records(root, DEAL_ROOMS_FILE), "room_id").values())
    if agent_id:
        rows = [r for r in rows if agent_id in (r.get("participants") or [])]
    if topic:
        t = topic.strip().lower()
        rows = [r for r in rows if t in str(r.get("topic", "")).lower()]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return [_public_room(r) for r in rows[offset : offset + limit]]


def get_deal_room(root: Path, room_id: str) -> dict[str, Any] | None:
    room = latest_by_id(load_records(root, DEAL_ROOMS_FILE), "room_id").get(room_id)
    if not room:
        return None
    return _public_room(room, include_messages=True)


def _public_room(room: dict[str, Any], *, include_messages: bool = False) -> dict[str, Any]:
    out = {
        "room_id": room.get("room_id"),
        "title": room.get("title"),
        "topic": room.get("topic"),
        "capabilities": room.get("capabilities", []),
        "host_agent_id": room.get("host_agent_id"),
        "participants": room.get("participants", []),
        "status": room.get("status"),
        "close_type": room.get("close_type"),
        "lead_routing_confirmed": room.get("lead_routing_confirmed"),
        "created_at": room.get("created_at"),
        "updated_at": room.get("updated_at"),
        "message_count": len(room.get("messages") or []),
        "handoff_signals": {
            "constitutional_chain": "entry → profit_guardrail → final_arbiter",
            "task_delegation": True,
            "confidence_required": True,
        },
    }
    if include_messages:
        out["messages"] = list(room.get("messages") or [])[-20:]
    return out


def post_deal_room_message(
    root: Path,
    *,
    room_id: str,
    agent_id: str,
    body: str,
    confidence: float | None = None,
) -> dict[str, Any]:
    rooms = latest_by_id(load_records(root, DEAL_ROOMS_FILE), "room_id")
    room = rooms.get(room_id)
    if not room:
        raise ValueError("deal room not found")
    if room.get("status") != "open":
        raise ValueError("deal room is not open")
    if agent_id not in (room.get("participants") or []):
        raise ValueError("agent is not a participant")

    clean = sanitize_profile_text(body, max_len=MAX_MESSAGE_LEN)
    if not clean:
        raise ValueError("message body is required")
    ok, err = scan_profile_field(root, clean, field="deal_room_message")
    if not ok:
        raise ValueError(err or "message failed security scan")

    from arclya2a.agents.reputation import guardrail_strictness

    strict = guardrail_strictness(root, agent_id)
    min_conf = float(strict.get("min_message_confidence", 70.0))
    conf = min_conf if confidence is None else max(0.0, min(100.0, float(confidence)))
    if conf < min_conf:
        raise ValueError(f"confidence must be at least {min_conf} for your trust tier")
    message = {
        "message_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "body": clean,
        "confidence": conf,
        "timestamp": _now(),
    }
    messages = list(room.get("messages") or [])
    messages.append(message)
    room["messages"] = messages[-MAX_MESSAGES:]
    room["updated_at"] = _now()
    append_record(root, DEAL_ROOMS_FILE, room)
    return message


def close_deal_room(
    root: Path,
    *,
    room_id: str,
    agent_id: str,
    close_type: str = "lead_routing_commitment",
    lead_routing_confirmed: bool = False,
    cta_url: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Close a deal room with A2A handoff signals (lead routing commitment preferred)."""
    if close_type not in _VALID_CLOSE_TYPES:
        raise ValueError(f"close_type must be one of: {sorted(_VALID_CLOSE_TYPES)}")

    rooms = latest_by_id(load_records(root, DEAL_ROOMS_FILE), "room_id")
    room = rooms.get(room_id)
    if not room:
        raise ValueError("deal room not found")
    if agent_id not in (room.get("participants") or []):
        raise ValueError("agent is not a participant")

    from arclya2a.agents.reputation import guardrail_strictness

    strict = guardrail_strictness(root, agent_id)
    min_conf = float(strict.get("min_close_confidence", 85.0))
    conf = min_conf if confidence is None else max(0.0, min(100.0, float(confidence)))
    if conf < min_conf:
        raise ValueError(f"close confidence must be at least {min_conf} for your trust tier")
    room["status"] = "closed"
    room["close_type"] = close_type
    room["lead_routing_confirmed"] = bool(lead_routing_confirmed)
    room["closed_by"] = agent_id
    room["close_confidence"] = conf
    room["updated_at"] = _now()
    if cta_url:
        room["cta_url"] = str(cta_url).strip()[:512]

    append_record(root, DEAL_ROOMS_FILE, room)

    if lead_routing_confirmed and close_type == "lead_routing_commitment":
        from arclya2a.agents.reputation import record_reputation_event

        for pid in room.get("participants") or []:
            record_reputation_event(
                root,
                agent_id=pid,
                event_type="deal_room_close",
                delta=5.0,
                source="deal_rooms",
                metadata={"room_id": room_id, "closed_by": agent_id},
            )

    return _public_room(room, include_messages=True)


def create_deal_room_micropayment(
    root: Path,
    *,
    room_id: str,
    payer_agent_id: str,
    amount_usd: float = 0.5,
    base_url: str,
) -> dict[str, Any]:
    """x402 micropayment intent for deal room activity (constitutional margin-positive)."""
    room = get_deal_room(root, room_id)
    if not room:
        raise ValueError("deal room not found")
    if payer_agent_id not in (room.get("participants") or []):
        raise ValueError("agent is not a participant")

    from arclya2a.payments.crypto import create_crypto_payment_intent, get_crypto_payment, is_crypto_payments_configured

    if not is_crypto_payments_configured():
        return {
            "micropayment_available": False,
            "reason": "crypto_not_configured",
            "room_id": room_id,
        }

    amount = max(0.1, round(float(amount_usd), 2))
    intent = create_crypto_payment_intent(
        root,
        amount_usd=amount,
        partner_id=payer_agent_id,
        deal_id=room_id,
        memo=f"Deal room micropayment {room_id}",
        metadata={
            "type": "deal_room_micropayment",
            "room_id": room_id,
            "host_agent_id": room.get("host_agent_id"),
        },
    )
    payment = get_crypto_payment(root, intent.payment_id)
    from arclya2a.payments.x402 import build_payment_required_payload

    x402 = build_payment_required_payload(
        payment or {},
        resource=f"/agents/hangout/deal-rooms/{room_id}/micropayment",
        description=f"Deal room micropayment for {room_id}",
    )
    return {
        "micropayment_available": True,
        "room_id": room_id,
        "amount_usd": amount,
        "currency": "USDC",
        "payment_id": intent.payment_id,
        "checkout_url": f"{base_url.rstrip('/')}/payments/crypto/{intent.payment_id}",
        "x402": x402,
        "x402_compatible": True,
    }