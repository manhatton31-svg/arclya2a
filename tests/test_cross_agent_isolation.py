"""Tests for cross-agent isolation in learning and partner scoring."""

from __future__ import annotations

import json

import pytest

from arclya2a.learning.patch_generator import generate_concrete_patches
from arclya2a.learning.prompt_updater import apply_learning_signal
from arclya2a.partners.test_registry import apply_security_event, get_test_partner, register_test_partner
from arclya2a.security.cross_agent_isolation import (
    apply_learning_signal_isolation,
    check_patch_isolation,
    filter_patches_by_isolation,
)
from arclya2a.security.security_analyzer import build_security_learning_context


def _security_signal_single_partner(partner_id: str) -> dict:
    return {
        "source": "security_data",
        "issues_detected": ["high_risk_partner", "sandbox_suspicious_partner"],
        "recommendations": [
            "Partners tp_bad triggered repeated injection blocks",
            "3 sandbox security events — tighten sandbox restrictions",
        ],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
        "injection_scans": {
            "blocks": 3,
            "by_partner": {partner_id: 3},
        },
        "sandbox_events": {
            "suspicious_events": 5,
            "by_partner": {partner_id: 5},
        },
    }


def test_partner_emergency_stop_does_not_affect_other_trust_scores(root):
    partner_a = register_test_partner(root, agent_name="Isolated Agent A")
    partner_b = register_test_partner(root, agent_name="Isolated Agent B")

    apply_security_event(root, partner_a["partner_id"], event_type="emergency_stop")
    apply_security_event(root, partner_a["partner_id"], event_type="emergency_stop")

    row_a = get_test_partner(root, partner_a["partner_id"])
    row_b = get_test_partner(root, partner_b["partner_id"])

    assert row_a["security"]["emergency_stop_count"] == 2
    assert row_b["security"]["emergency_stop_count"] == 0
    assert row_a["security"]["behavior_score"] < row_b["security"]["behavior_score"]


def test_single_partner_high_risk_excluded_from_global_issues():
    signal = _security_signal_single_partner("tp_bad001")
    isolated = apply_learning_signal_isolation(signal)

    assert "high_risk_partner" not in isolated["issues_detected"]
    assert "high_risk_partner" in isolated["isolation"]["excluded_issues"]
    assert "tp_bad001" in isolated["isolation"]["partner_scoped_issues"]
    assert isolated["isolation"]["allows_global_patch"] is False


def test_sandbox_issues_isolated_from_production_patches():
    signal = {
        "issues_detected": ["sandbox_suspicious_partner", "tool_gate_violation"],
        "recommendations": ["sandbox events", "tool gate blocks"],
        "sandbox_events": {"suspicious_events": 4, "by_partner": {"tp_x": 4}},
        "tool_gate_blocks": {"total_blocks": 3},
    }
    isolated = apply_learning_signal_isolation(signal)

    assert "sandbox_suspicious_partner" not in isolated["issues_detected"]
    assert "tool_gate_violation" in isolated["issues_detected"]
    assert isolated["isolation"]["sandbox_isolated_from_production"] is True


def test_two_partners_allow_global_high_risk_patch():
    signal = {
        "issues_detected": ["high_risk_partner"],
        "recommendations": ["Partners tp_a, tp_b triggered blocks"],
        "injection_scans": {
            "by_partner": {"tp_a": 3, "tp_b": 2},
        },
    }
    isolated = apply_learning_signal_isolation(signal)

    assert "high_risk_partner" in isolated["issues_detected"]
    assert isolated["isolation"]["allows_global_patch"] is True
    assert isolated["isolation"]["distinct_partner_count"] == 2


def test_filter_patches_blocks_sandbox_only_issues():
    signal = apply_learning_signal_isolation({
        "issues_detected": ["sandbox_suspicious_partner"],
        "recommendations": ["sandbox"],
        "sandbox_events": {"suspicious_events": 4, "by_partner": {"tp_1": 4}},
    })
    # Signal-level isolation removes sandbox issues from global patch generation.
    assert "sandbox_suspicious_partner" not in signal["issues_detected"]
    assert not generate_concrete_patches(signal)

    # If a sandbox patch were proposed manually, the isolation gate still blocks it.
    manual_patch = {
        "issue": "sandbox_suspicious_partner",
        "target_prompt": "prompts/closer_prompt.md",
        "patch_kind": "prompt",
        "evidence": {"isolation": signal["isolation"]},
    }
    check = check_patch_isolation(manual_patch, signal)
    assert check.allowed is False


def test_check_patch_isolation_blocks_single_partner_broad_patch():
    patch = {
        "issue": "high_risk_partner",
        "target_prompt": "prompts/closer_prompt.md",
        "patch_kind": "prompt",
        "evidence": {
            "isolation": {
                "allows_global_patch": False,
                "attributed_partners": ["tp_only"],
                "min_actors_for_global_patch": 2,
            }
        },
    }
    result = check_patch_isolation(patch)
    assert result.allowed is False
    assert result.broad_impact is True


def test_apply_learning_signal_skips_single_partner_global_patches(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "0")
    isolated = apply_learning_signal_isolation({
        "issues_detected": ["high_risk_partner"],
        "recommendations": ["single partner"],
        "meta_optimizer_target": "prompts/closer_prompt.md",
        "injection_scans": {"by_partner": {"tp_solo": 5}},
    })
    signal = {"improvement_signal": isolated}
    result = apply_learning_signal(root, signal, auto_apply_low_risk=False)
    assert result["patches_created"] == 0
    assert "high_risk_partner" in isolated["isolation"]["excluded_issues"]


def test_build_security_learning_context_includes_isolation(root):
    scan_path = root / "learning" / "injection_scan_events.jsonl"
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        row = {
            "agent_id": "closer",
            "recommended_action": "disqualify",
            "is_suspicious": True,
            "partner_id": "tp_iso_test",
            "detected_patterns": [{"id": "instruction_override", "severity": 0.95, "excerpt": "ignore previous"}],
        }
        with open(scan_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    ctx = build_security_learning_context(root)
    assert "isolation" in ctx
    assert "by_partner" in ctx
    assert ctx["isolation"].get("excluded_issues") is not None