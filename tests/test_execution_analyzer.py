"""Tests for execution analyzer and patch generator."""

from __future__ import annotations

import json as json_module

import pytest

from arclya2a.learning.execution_analyzer import (
    analyze_billing_data,
    analyze_demo_phases,
    analyze_tool_executions,
    build_execution_learning_context,
    emit_execution_learning_signal,
)
from arclya2a.learning.patch_generator import (
    apply_change_to_prompt,
    apply_patch_by_id,
    generate_concrete_patches,
    list_patches,
    store_prompt_patches,
)
from arclya2a.learning.prompt_updater import apply_learning_signal


def _demo_report_with_tools():
    return {
        "success": True,
        "executive_summary": {
            "onboarding_complete": True,
            "deal_closed": True,
            "lead_routing_confirmed": True,
        },
        "phases": [
            {"name": "onboarding", "onboarding_complete": True, "guardrails_ok": True},
            {"name": "recruiter", "recruiter_skips_onboarding": True, "guardrails_ok": True},
            {
                "name": "closer",
                "deal_closed": True,
                "lead_routing_confirmed": True,
                "tools_executed": 0,
                "tool_results": [],
                "guardrails_ok": True,
            },
        ],
        "guardrails": {"phases_verified": True},
    }


def test_analyze_demo_phases_no_tools_on_close():
    analysis = analyze_demo_phases(_demo_report_with_tools())
    assert "demo_no_tools_on_close" in analysis["issues"]


def test_analyze_demo_phases_tools_before_close():
    report = _demo_report_with_tools()
    report["phases"][2]["deal_closed"] = False
    report["phases"][2]["tool_results"] = [{"tool_id": "gmail.send_followup_email", "outcome": "dry_run"}]
    analysis = analyze_demo_phases(report)
    assert "tools_called_before_close" in analysis["issues"]


def test_build_execution_learning_context(root):
    ctx = build_execution_learning_context(root, _demo_report_with_tools())
    assert "tool_executions" in ctx
    assert "billing" in ctx
    assert ctx.get("issues_detected") is not None


def test_generate_concrete_patches_from_signal():
    signal = {
        "issues_detected": ["tools_called_before_close", "demo_no_tools_on_close"],
        "recommendations": ["Fix tool timing"],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patches = generate_concrete_patches(signal)
    assert len(patches) >= 2
    assert all(p.get("status") == "pending" for p in patches)
    assert patches[0].get("changes")


def test_generate_concrete_patches_minimal_insert_content():
    signal = {
        "issues_detected": ["negotiation_too_short"],
        "recommendations": [],
        "priority": "medium",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patches = generate_concrete_patches(signal)
    content = patches[0]["changes"][0]["content"]
    assert len(content) < 120


def test_store_and_list_patches(root):
    signal = {
        "issues_detected": ["closer_no_commitment"],
        "recommendations": ["Improve close"],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patches = generate_concrete_patches(signal)
    ids = store_prompt_patches(root, patches)
    assert ids
    listed = list_patches(root, status="pending")
    assert any(p["patch_id"] == ids[0] for p in listed)


def test_apply_change_insert_after():
    text = "### Anchor\n- existing rule\n"
    change = {"type": "insert_after", "anchor": "### Anchor", "content": "- new rule"}
    result = apply_change_to_prompt(text, change)
    assert "new rule" in result


def test_apply_learning_signal_creates_patches(root):
    signal = {
        "improvement_signal": {
            "issues_detected": ["negotiation_too_short"],
            "recommendations": ["Require more turns"],
            "priority": "medium",
            "meta_optimizer_target": "prompts/closer_prompt.md",
        }
    }
    result = apply_learning_signal(root, signal, auto_apply=False, auto_apply_low_risk=False)
    assert result["patches_created"] >= 1
    assert result["pending_review"] >= 1
    assert result["patches_applied"] == 0


def test_apply_patch_by_id(root):
    signal = {
        "issues_detected": ["objections_not_documented"],
        "recommendations": ["Document objections"],
        "priority": "medium",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patches = generate_concrete_patches(signal)
    patch_id = store_prompt_patches(root, patches)[0]
    result = apply_patch_by_id(root, patch_id)
    assert result.get("applied") is True


def test_emit_execution_learning_signal(root):
    signal = emit_execution_learning_signal(root, _demo_report_with_tools())
    assert signal["source"] == "execution_data"
    path = root / "learning" / "execution_signals.jsonl"
    assert path.exists()


def test_analyze_tool_executions_empty(root):
    analysis = analyze_tool_executions(root, limit=10)
    assert "failure_rate" in analysis


def test_analyze_billing_data(root):
    analysis = analyze_billing_data(root)
    assert "deal_count" in analysis