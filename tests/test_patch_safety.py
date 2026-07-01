"""Tests for patch safety classification, auto-apply rules, and outcomes tracking."""

from __future__ import annotations

import json

import pytest

from arclya2a.learning.patch_generator import (
    auto_apply_eligible_patches,
    generate_concrete_patches,
    store_prompt_patches,
)
from arclya2a.learning.patch_outcomes import (
    build_dashboard,
    evaluate_patch_outcomes,
    patch_success_stats,
    record_patch_applied,
)
from arclya2a.learning.patch_safety import (
    PatchValidation,
    classify_patch_risk,
    compute_patch_confidence,
    enrich_patch,
    should_auto_apply,
    validate_patch,
)
from arclya2a.learning.prompt_updater import apply_learning_signal


def _patch(issue: str, *, content: str = "- minor wording fix") -> dict:
    return {
        "patch_id": f"closer_prompt_{issue}_test",
        "issue": issue,
        "status": "pending",
        "target_prompt": "prompts/closer_prompt.md",
        "agent_id": "closer_prompt",
        "changes": [{
            "type": "insert_after",
            "anchor": "## Quality Bar",
            "content": content,
        }],
        "evidence": {"issues_detected": [issue]},
    }


def test_classify_low_risk_reinforcement():
    patch = _patch("reinforcement")
    assert classify_patch_risk(patch) == "low_risk"


def test_classify_high_risk_tool_timing():
    patch = _patch("tools_called_before_close", content="- tool gate rule")
    assert classify_patch_risk(patch) == "high_risk"


def test_classify_medium_risk_objections():
    patch = _patch("objections_not_documented", content="- log objections")
    assert classify_patch_risk(patch) == "medium_risk"


def test_compute_patch_confidence_with_anchor(root):
    patch = enrich_patch(root, _patch("reinforcement", content="- short fix"))
    assert 0.0 <= patch["confidence"] <= 1.0
    assert patch["confidence"] >= 0.7


def test_validate_patch_ok(root):
    patch = _patch("reinforcement")
    result = validate_patch(root, patch)
    assert result.ok is True
    assert not result.errors


def test_validate_patch_missing_target(root, tmp_path):
    patch = _patch("reinforcement")
    patch["target_prompt"] = "prompts/nonexistent.md"
    result = validate_patch(tmp_path, patch)
    assert result.ok is False
    assert any("missing" in e.lower() for e in result.errors)


def test_should_auto_apply_low_risk():
    patch = {
        "risk_class": "low_risk",
        "confidence": 0.85,
        "status": "pending",
    }
    validation = PatchValidation(ok=True, errors=[], warnings=[])
    assert should_auto_apply(patch, validation) is True


def test_should_not_auto_apply_high_risk():
    patch = {
        "risk_class": "high_risk",
        "confidence": 0.95,
        "status": "pending",
    }
    validation = PatchValidation(ok=True, errors=[], warnings=[])
    assert should_auto_apply(patch, validation) is False


def test_enrich_patch_adds_metadata(root):
    patch = enrich_patch(root, _patch("reinforcement"))
    assert patch["risk_class"] == "low_risk"
    assert "confidence" in patch
    assert "diff_preview" in patch
    assert "validation" in patch
    assert "auto_apply_eligible" in patch


def test_store_patches_include_risk_and_confidence(root):
    signal = {
        "issues_detected": ["negotiation_too_short"],
        "recommendations": ["Require more turns"],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patches = generate_concrete_patches(signal)
    patch_id = store_prompt_patches(root, patches)[0]
    stored = json.loads((root / "learning" / "prompt_patches" / f"{patch_id}.json").read_text())
    assert stored["risk_class"] == "high_risk"
    assert stored["confidence"] is not None
    assert stored["auto_apply_eligible"] is False


def test_auto_apply_low_risk_reinforcement(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "1")
    signal = {
        "improvement_signal": {
            "recommendations": ["Use clearer CTAs", "Add one concrete example"],
            "priority": "low",
            "meta_optimizer_target": "prompts/closer_prompt.md",
        }
    }
    result = apply_learning_signal(root, signal, auto_apply=False, auto_apply_low_risk=True)
    assert result["patches_created"] >= 1
    assert result["patches_applied"] >= 1
    assert any(r.get("auto_applied") for r in result.get("auto_applied", []))


def test_high_risk_patches_not_auto_applied(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "1")
    signal = {
        "improvement_signal": {
            "issues_detected": ["negotiation_too_short"],
            "recommendations": ["Require more turns"],
            "priority": "high",
            "meta_optimizer_target": "prompts/closer_prompt.md",
        }
    }
    result = apply_learning_signal(root, signal, auto_apply=False, auto_apply_low_risk=True)
    assert result["patches_created"] >= 1
    assert result["patches_applied"] == 0
    assert result["pending_review"] >= 1


def test_auto_apply_eligible_patches_skips_high_risk(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "1")
    signal = {
        "issues_detected": ["tools_called_before_close"],
        "priority": "high",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    patch_ids = store_prompt_patches(root, generate_concrete_patches(signal))
    results = auto_apply_eligible_patches(root, patch_ids)
    assert all(not r.get("applied") for r in results)


def test_record_and_evaluate_patch_outcomes(root):
    patch = enrich_patch(root, _patch("reinforcement"))
    record_patch_applied(root, patch, baseline_issues=["reinforcement"])
    result = evaluate_patch_outcomes(root, [])
    assert any(u.get("outcome") == "resolved" for u in result.get("updated", []))


def test_patch_success_stats(root):
    patch = enrich_patch(root, _patch("reinforcement"))
    record_patch_applied(root, patch)
    evaluate_patch_outcomes(root, [])
    stats = patch_success_stats(root)
    assert stats["tracked"] >= 1
    assert stats["resolved"] >= 1
    assert stats["success_rate"] is not None


def test_build_dashboard(root):
    signal = {
        "issues_detected": ["objections_not_documented"],
        "recommendations": ["Document objections"],
        "priority": "medium",
        "meta_optimizer_target": "prompts/closer_prompt.md",
    }
    store_prompt_patches(root, generate_concrete_patches(signal))
    dashboard = build_dashboard(root)
    assert "pending_count" in dashboard
    assert "pending_by_risk" in dashboard
    assert "recent_applied" in dashboard
    assert "outcome_stats" in dashboard
    assert "issue_summary" in dashboard
    assert "recent_learning_runs" in dashboard
    assert "scheduler" in dashboard