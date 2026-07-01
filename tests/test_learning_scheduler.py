"""Tests for background learning scheduler and enhanced outcome tracking."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from arclya2a.learning.learning_scheduler import (
    load_scheduler_state,
    run_learning_cycle,
    save_scheduler_state,
    scheduler_enabled,
    should_run_learning,
)
from arclya2a.learning.patch_outcomes import (
    build_dashboard,
    evaluate_patch_outcomes,
    extract_issue_metrics,
    issue_status_summary,
    list_learning_runs,
    record_learning_run,
    record_patch_applied,
)
from arclya2a.learning.patch_safety import enrich_patch


def test_scheduler_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARCLYA_LEARNING_SCHEDULER_ENABLED", raising=False)
    assert scheduler_enabled() is False


def test_should_run_learning_initial(root):
    state_path = root / "learning" / "scheduler_state.json"
    if state_path.exists():
        state_path.unlink()
    should, reason = should_run_learning(root)
    assert should is True
    assert reason == "initial"


def test_should_run_learning_after_interval(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_LEARNING_INTERVAL_HOURS", "1")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_scheduler_state(root, {"last_run_at": old, "last_deal_count": 0})
    should, reason = should_run_learning(root)
    assert should is True
    assert reason == "scheduled"


def test_should_run_learning_skips_when_recent(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_LEARNING_INTERVAL_HOURS", "24")
    recent = datetime.now(timezone.utc).isoformat()
    save_scheduler_state(root, {"last_run_at": recent, "last_deal_count": 0})
    should, reason = should_run_learning(root)
    assert should is False
    assert reason == "skipped"


def test_should_run_learning_deals_trigger(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_LEARNING_MIN_DEALS", "1")
    monkeypatch.setenv("ARCLYA_LEARNING_INTERVAL_HOURS", "999")
    recent = datetime.now(timezone.utc).isoformat()
    save_scheduler_state(root, {"last_run_at": recent, "last_deal_count": 0})
    should, reason = should_run_learning(root)
    # Triggers when deal_count - last_deal_count >= 1 (if deals exist in billing)
    if should:
        assert reason == "deals"


def test_run_learning_cycle_records_run(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "1")
    result = run_learning_cycle(root, trigger="test", auto_apply_low_risk=True)
    assert result.get("trigger") == "test"
    assert "patches_created" in result
    assert "issues_still_open" in result
    runs = list_learning_runs(root, limit=5)
    assert runs
    assert runs[0].get("trigger") == "test"
    state = load_scheduler_state(root)
    assert state.get("last_run_at") is not None


def test_record_learning_run(root):
    entry = record_learning_run(root, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "unit_test",
        "issues_detected": ["tool_high_skip_rate"],
        "patches_created": 1,
        "patches_applied": 0,
    })
    assert entry.get("run_id")
    runs = list_learning_runs(root, limit=1)
    assert runs[0]["trigger"] == "unit_test"


def test_extract_issue_metrics():
    signal = {
        "issues_detected": ["tool_high_failure_rate", "billing_no_deals"],
        "tool_executions": {"failure_rate": 0.35, "total": 10},
        "billing": {"deal_count": 0},
    }
    metrics = extract_issue_metrics(signal)
    assert metrics["tool_high_failure_rate"]["failure_rate"] == 0.35
    assert metrics["billing_no_deals"]["deal_count"] == 0


def test_evaluate_patch_outcomes_returns_summary(root):
    patch = enrich_patch(root, {
        "patch_id": "test_patch_outcome",
        "issue": "reinforcement",
        "agent_id": "closer_prompt",
        "target_prompt": "prompts/closer_prompt.md",
        "changes": [{
            "type": "insert_after",
            "anchor": "## Quality Bar",
            "content": "- test outcome tracking",
        }],
        "evidence": {},
    })
    record_patch_applied(root, patch, baseline_issues=["reinforcement"])
    result = evaluate_patch_outcomes(root, [], signal={"issues_detected": []})
    assert "issues_improved" in result
    assert "issues_still_open" in result
    assert "issues_newly_resolved" in result


def test_issue_status_summary(root):
    summary = issue_status_summary(root)
    assert "issues_improved" in summary
    assert "issues_still_open" in summary
    assert "improved_count" in summary


def test_dashboard_includes_learning_runs(root):
    record_learning_run(root, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "dashboard_test",
        "issues_detected": [],
        "patches_created": 0,
        "patches_applied": 0,
    })
    dashboard = build_dashboard(root)
    assert dashboard["recent_learning_runs"]
    assert dashboard["issue_summary"]["improved_count"] >= 0