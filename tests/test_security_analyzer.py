"""Tests for security incident analysis and defensive learning integration."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from arclya2a.audit.logger import append_audit_record
from arclya2a.learning.patch_generator import generate_concrete_patches, store_prompt_patches
from arclya2a.learning.learning_scheduler import run_learning_cycle
from arclya2a.security.injection_scanner import record_scan_event, InjectionScanResult
from arclya2a.security.security_analyzer import (
    analyze_injection_scans,
    analyze_tool_gate_blocks,
    build_security_learning_context,
    emit_security_learning_signal,
    log_security_incident,
    security_patch_outcome_stats,
)


def _scan_event(
    *,
    agent_id: str = "closer",
    action: str = "disqualify",
    pattern_id: str = "instruction_override",
):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "recommended_action": action,
        "is_suspicious": True,
        "confidence": 0.95,
        "detected_patterns": [{
            "id": pattern_id,
            "label": "Direct instruction override",
            "severity": 0.95,
            "source": "task_context",
            "excerpt": "ignore previous instructions now",
        }],
    }


def test_analyze_injection_scans_detects_blocks():
    events = [_scan_event(), _scan_event(pattern_id="role_hijack")]
    analysis = analyze_injection_scans(events)
    assert "injection_scan_rejection" in analysis["issues"]
    assert "injection_scan_disqualify" in analysis["issues"]
    assert analysis["blocks"] == 2


def test_analyze_injection_scans_repeated_pattern():
    events = [_scan_event(), _scan_event(), _scan_event()]
    analysis = analyze_injection_scans(events)
    assert "repeated_injection_pattern" in analysis["issues"]
    assert analysis["suggested_patterns"]


def test_analyze_tool_gate_blocks(root):
    append_audit_record(
        root,
        agent_id="closer",
        action="tool_gate_blocked",
        reasoning="blocked",
        metadata={
            "category": "tool_gating",
            "tool_id": "gmail.send_followup_email",
            "blocked_reason_code": "PARTNER_REQUEST_NOT_GATE",
        },
    )
    append_audit_record(
        root,
        agent_id="closer",
        action="tool_gate_blocked",
        reasoning="blocked",
        metadata={
            "category": "tool_gating",
            "tool_id": "linear.create_followup_task",
            "blocked_reason_code": "COMMITMENT_NOT_CONFIRMED",
        },
    )
    from arclya2a.security.security_analyzer import load_tool_gate_blocks

    blocks = load_tool_gate_blocks(root)
    analysis = analyze_tool_gate_blocks(blocks)
    assert "tool_gate_violation" in analysis["issues"]
    assert "tool_gate_partner_command" in analysis["issues"]
    assert "tool_gate_premature" in analysis["issues"]


def test_log_security_incident_writes_jsonl(root):
    log_security_incident(
        root,
        "injection_scan_block",
        agent_id="closer",
        details={"patterns": ["instruction_override"]},
    )
    path = root / "learning" / "security_signals.jsonl"
    assert path.exists()
    row = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["event_type"] == "incident"
    assert row["incident_type"] == "injection_scan_block"


def test_emit_security_learning_signal(root):
    scan_path = root / "learning" / "injection_scan_events.jsonl"
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    with open(scan_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_scan_event()) + "\n")
        f.write(json.dumps(_scan_event()) + "\n")

    signal = emit_security_learning_signal(root)
    assert signal["source"] == "security_data"
    assert signal.get("issues_detected")
    out = root / "learning" / "security_signals.jsonl"
    assert out.exists()


def test_build_security_learning_context(root):
    ctx = build_security_learning_context(root)
    assert "injection_scans" in ctx
    assert "tool_gate_blocks" in ctx
    assert ctx.get("patch_category") == "defensive"


def test_generate_defensive_patches_from_security_signal():
    signal = {
        "source": "security_data",
        "issues_detected": ["tool_gate_partner_command", "repeated_injection_pattern"],
        "recommendations": ["Strengthen tool gate"],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
        "suggested_patterns": [{
            "pattern_id": "learned_test",
            "label": "Test pattern",
            "regex": "(?i)ignore previous",
            "severity": 0.8,
            "occurrences": 3,
        }],
    }
    patches = generate_concrete_patches(signal)
    kinds = {p.get("patch_kind") for p in patches}
    issues = {p.get("issue") for p in patches}
    assert "injection_pattern" in kinds
    assert "tool_gate_partner_command" in issues


def test_record_scan_event_logs_incident(root):
    result = InjectionScanResult(
        is_suspicious=True,
        confidence=0.9,
        detected_patterns=[{"id": "instruction_override", "severity": 0.9}],
        recommended_action="reject",
        agent_id="closer",
        scan_id="scan_test",
    )
    record_scan_event(root, result, deal_id="d1")
    incidents = [
        json.loads(ln) for ln in (root / "learning" / "security_signals.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(i.get("incident_type") == "injection_scan_block" for i in incidents)


def test_run_learning_cycle_includes_security(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "0")
    result = run_learning_cycle(root, trigger="security_test", auto_apply_low_risk=False)
    assert "security_incident_total" in result
    assert "security_issues" in result


def test_security_patch_outcome_stats(root):
    stats = security_patch_outcome_stats(root)
    assert "tracked_security_patches" in stats
    assert "recent_incidents_7d" in stats