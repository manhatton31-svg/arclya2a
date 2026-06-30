import pytest

from arclya2a.handoff.validators import (
    HandoffValidationError,
    build_memory_summary,
    merge_ssot,
    validate_emergency_stop,
    validate_handoff,
    validate_preference_handshake,
    validate_role_card,
    validate_structured_feedback,
)


def _valid_handoff(**overrides):
    base = {
        "agent_id": "outreach_worker",
        "status": "COMPLETE",
        "next_action": "handoff_to_profit_guardrail",
        "ssot": {"deal_id": "d1", "summary": "Test deal", "stage": "new"},
        "memory_summary": "[d1] stage=new: Test deal",
        "validation": {"confidence": 80, "check": "ok"},
    }
    base.update(overrides)
    return base


def test_validate_complete_handoff():
    result = validate_handoff(_valid_handoff())
    assert result["status"] == "COMPLETE"


def test_complete_requires_next_action():
    payload = _valid_handoff(next_action="")
    with pytest.raises(HandoffValidationError):
        validate_handoff(payload)


def test_emergency_stop():
    payload = _valid_handoff(status="EMERGENCY_STOP", next_action="halt")
    validate_emergency_stop(payload)
    validate_handoff(payload)


def test_role_card_max_two_sentences():
    validate_role_card("First sentence. Second sentence.")
    with pytest.raises(HandoffValidationError):
        validate_role_card("One. Two. Three.")


def test_preference_handshake_defaults():
    hs = validate_preference_handshake(None)
    assert hs["format"] == "json"
    assert hs["accepted"] is True


def test_structured_feedback_length():
    fb = validate_structured_feedback({"message": "ok", "severity": "info"})
    assert fb["message"] == "ok"
    with pytest.raises(HandoffValidationError):
        validate_structured_feedback({"message": "x" * 501})


def test_merge_ssot_and_memory():
    ssot = {"deal_id": "d1", "summary": "Hello", "stage": "new", "metadata": {"a": 1}}
    merged = merge_ssot(ssot, {"stage": "draft", "metadata": {"b": 2}})
    assert merged["stage"] == "draft"
    assert merged["metadata"] == {"a": 1, "b": 2}
    mem = build_memory_summary(merged)
    assert "d1" in mem and "draft" in mem