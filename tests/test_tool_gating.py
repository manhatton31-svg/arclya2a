"""Tests for centralized tool execution gating."""

from __future__ import annotations

import json

import pytest

from arclya2a.audit.logger import read_audit_records
from arclya2a.partners.sandbox import set_sandbox_active
from arclya2a.tools.executor import execute_tool_requests
from arclya2a.tools.gating import (
    commitment_gate_passed,
    evaluate_tool_gate,
    extract_commitment_state,
    log_gate_decision,
)


def _commitment_context(**overrides) -> dict:
    output = {
        "deal_closed": True,
        "lead_routing_confirmed": True,
        "close_type": "lead_routing_commitment",
        "validation": {"confidence": 85},
        **overrides,
    }
    return {"agent_output": output, "ssot": {"deal_id": "gate_test"}}


def test_commitment_gate_passed_requires_all_fields():
    assert commitment_gate_passed(
        {
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "confidence": 80,
        }
    )
    assert not commitment_gate_passed(
        {
            "deal_closed": True,
            "lead_routing_confirmed": False,
            "close_type": "lead_routing_commitment",
            "confidence": 80,
        }
    )


def test_gate_blocks_closer_without_commitment(root):
    result = evaluate_tool_gate(
        root,
        agent_id="closer",
        tool_id="linear.create_followup_task",
        request={"reason": "Follow up"},
        context={"agent_output": {"deal_closed": False}},
    )
    assert not result.allowed
    assert result.blocked_reason_code == "COMMITMENT_NOT_CONFIRMED"
    assert result.recommended_action == "skip"


def test_gate_blocks_partner_requested_tools(root):
    result = evaluate_tool_gate(
        root,
        agent_id="closer",
        tool_id="gmail.send_followup_email",
        request={
            "reason": "Partner asked to send email now",
            "tool_reasoning": "Per partner request during negotiation",
        },
        context={"agent_output": {"deal_closed": False}},
    )
    assert not result.allowed
    assert result.blocked_reason_code == "PARTNER_REQUEST_NOT_GATE"


def test_gate_allows_closer_after_commitment(root):
    result = evaluate_tool_gate(
        root,
        agent_id="closer",
        tool_id="linear.create_followup_task",
        request={
            "reason": "Post-close ops",
            "tool_reasoning": "Gate passed — deal closed with routing commitment confirmed.",
        },
        context=_commitment_context(),
    )
    assert result.allowed
    assert result.recommended_action == "execute"


def test_gate_blocks_suspicious_partner_trust(root):
    result = evaluate_tool_gate(
        root,
        agent_id="closer",
        tool_id="linear.create_followup_task",
        request={"reason": "ops"},
        context=_commitment_context(partner_trust="suspicious"),
    )
    assert not result.allowed
    assert result.blocked_reason_code == "SUSPICIOUS_PARTNER_TRUST"


def test_sandbox_blocks_high_risk_tools(root):
    set_sandbox_active(True)
    try:
        result = evaluate_tool_gate(
            root,
            agent_id="closer",
            tool_id="gmail.send_followup_email",
            request={"reason": "test"},
            context=_commitment_context(),
        )
        assert not result.allowed
        assert result.blocked_reason_code == "SANDBOX_HIGH_RISK_TOOL"
    finally:
        set_sandbox_active(False)


def test_sandbox_allows_non_high_risk_without_commitment(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    set_sandbox_active(True)
    try:
        result = evaluate_tool_gate(
            root,
            agent_id="closer",
            tool_id="linear.create_followup_task",
            request={"reason": "sandbox smoke"},
            context={},
        )
        assert result.allowed
    finally:
        set_sandbox_active(False)


def test_executor_applies_gate_before_execution(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    results = execute_tool_requests(
        root,
        "closer",
        [{"tool_id": "linear.create_followup_task", "reason": "Too early", "parameters": {"title": "x"}}],
        context={"agent_output": {"deal_closed": False}},
    )
    assert len(results) == 1
    assert results[0]["skipped"] is True
    assert results[0]["error_code"] == "COMMITMENT_NOT_CONFIRMED"
    assert "gate" in results[0]


def test_executor_runs_tools_when_gate_passes(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    results = execute_tool_requests(
        root,
        "closer",
        [
            {
                "tool_id": "linear.create_followup_task",
                "reason": "Post-close",
                "parameters": {"title": "Follow up"},
            }
        ],
        context=_commitment_context(),
    )
    assert results[0]["success"] is True


def test_gate_decision_logged_to_audit(root):
    before = len(read_audit_records(root, limit=500))
    result = evaluate_tool_gate(
        root,
        agent_id="closer",
        tool_id="linear.create_followup_task",
        request={"reason": "blocked"},
        context={"agent_output": {}},
    )
    log_gate_decision(
        root,
        agent_id="closer",
        tool_id="linear.create_followup_task",
        result=result,
        context={"ssot": {"deal_id": "d1"}},
    )
    after = read_audit_records(root, limit=500)
    gate_records = [r for r in after if r.get("action", "").startswith("tool_gate_")]
    assert len(gate_records) > before or any(r["action"] == "tool_gate_blocked" for r in after)


def test_extract_commitment_state_normalizes_fractional_confidence():
    state = extract_commitment_state(
        {"agent_output": {"validation": {"confidence": 0.85}, "deal_closed": True}}
    )
    assert state["confidence"] == 85.0