"""Pure validators for Strong Handoff Protocol."""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

import jsonschema


class HandoffValidationError(Exception):
    """Raised when a handoff payload fails validation."""


def _load_schema() -> dict:
    schema_file = resources.files("arclya2a.schemas").joinpath("handoff.json")
    with schema_file.open(encoding="utf-8") as f:
        return json.load(f)


def validate_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate handoff against JSON schema and constitutional rules."""
    schema = _load_schema()
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as e:
        raise HandoffValidationError(str(e)) from e

    status = payload.get("status")
    if status == "COMPLETE":
        if not payload.get("next_action"):
            raise HandoffValidationError("COMPLETE requires next_action")
        if not payload.get("memory_summary"):
            raise HandoffValidationError("COMPLETE requires memory_summary")
        validation = payload.get("validation", {})
        conf = validation.get("confidence")
        if conf is None or not (0 <= conf <= 100):
            raise HandoffValidationError("validation.confidence must be 0-100")

    if status == "EMERGENCY_STOP":
        validate_emergency_stop(payload)

    return payload


def validate_emergency_stop(payload: dict[str, Any]) -> None:
    """Validate EMERGENCY_STOP kill switch payload."""
    if payload.get("status") != "EMERGENCY_STOP":
        raise HandoffValidationError("Not an EMERGENCY_STOP payload")
    if not payload.get("next_action"):
        raise HandoffValidationError("EMERGENCY_STOP requires next_action")


def validate_role_card(role_card: str) -> None:
    """Strict Role Cards: max 2 sentences."""
    if not role_card or not role_card.strip():
        raise HandoffValidationError("Role card cannot be empty")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", role_card.strip()) if s.strip()]
    if len(sentences) > 2:
        raise HandoffValidationError(f"Role card exceeds 2 sentences: {len(sentences)}")


def validate_preference_handshake(handshake: dict[str, Any] | None) -> dict[str, Any]:
    """Validate preference handshake for context formats."""
    if handshake is None:
        return {"format": "json", "accepted": True, "preferences": {}}
    if "format" not in handshake:
        raise HandoffValidationError("preference_handshake requires format")
    if "accepted" not in handshake:
        raise HandoffValidationError("preference_handshake requires accepted")
    return handshake


def validate_structured_feedback(feedback: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate short structured feedback from receiving agent."""
    if feedback is None:
        return None
    if not feedback.get("message"):
        raise HandoffValidationError("feedback requires message")
    if len(feedback["message"]) > 500:
        raise HandoffValidationError("feedback.message exceeds 500 chars")
    if feedback.get("severity") not in (None, "info", "warn", "error"):
        raise HandoffValidationError("feedback.severity must be info|warn|error")
    return feedback


def merge_ssot(current: dict[str, Any], updates: dict[str, Any] | None) -> dict[str, Any]:
    """Merge SSOT updates immutably."""
    merged = dict(current)
    if updates:
        for key, value in updates.items():
            if key == "metadata" and isinstance(value, dict) and isinstance(merged.get("metadata"), dict):
                merged["metadata"] = {**merged["metadata"], **value}
            else:
                merged[key] = value
    return merged


def build_memory_summary(ssot: dict[str, Any]) -> str:
    """Build compact memory summary synced from SSOT."""
    deal_id = ssot.get("deal_id", "unknown")
    stage = ssot.get("stage", "unknown")
    summary = ssot.get("summary", "")
    short = summary[:120] + ("..." if len(summary) > 120 else "")
    return f"[{deal_id}] stage={stage}: {short}"