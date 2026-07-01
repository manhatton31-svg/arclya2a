"""Tests for partner journey progress and funnel metrics."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.observability.security_events import build_security_metrics
from arclya2a.partners.progress import (
    build_partner_funnel_metrics,
    build_partner_progress,
    recommend_next_step,
)
from arclya2a.observability.dashboard import format_ops_dashboard_text
from arclya2a.observability.security_events import format_security_dashboard_text
from arclya2a.partners.graduation import graduate_partner
from arclya2a.partners.sandbox import register_sandbox_key
from arclya2a.partners.test_registry import record_partner_activity, register_test_partner
from arclya2a.server.app import create_app


def _unique_name() -> str:
    return f"ProgressAgent_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def relax_sandbox_register_limits(monkeypatch):
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_REGISTER_PER_IP_DAY", "1000")
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_KEYS_PER_AGENT", "10")


def test_recommend_next_step_for_new_partner():
    milestones = {mid: False for mid in [
        "profile_validated", "onboarding_complete", "recruitment_reviewed",
        "close_dry_run", "no_emergency_stops", "security_score_ok",
    ]}
    step = recommend_next_step(milestones)
    assert step["id"] == "validate_profile"


def test_build_partner_progress_includes_milestones(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    progress = build_partner_progress(root, partner["partner_id"])
    assert progress is not None
    assert progress["milestone_progress"]["total"] == 6
    assert progress["next_step"]["id"] == "validate_profile"
    assert "success_definition" in progress


def test_build_partner_funnel_metrics_counts(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    record_partner_activity(root, partner["partner_id"], event="profile_validated")
    funnel = build_partner_funnel_metrics(root)
    assert funnel["registrations"] >= 1
    assert funnel["profile_validated"] >= 1
    assert "funnel_stages" in funnel
    assert len(funnel["funnel_stages"]) == 7
    stage_names = [s["stage"] for s in funnel["funnel_stages"]]
    assert stage_names[-1] == "graduated"
    assert funnel["conversion_rates"]["registration_to_validated"] is not None


def test_ops_dashboard_includes_partner_funnel(root):
    register_test_partner(root, agent_name=_unique_name())
    dashboard = build_ops_dashboard(root)
    assert "partners" in dashboard
    assert dashboard["partners"]["registrations"] >= 1
    assert "graduated" in dashboard["partners"]
    assert "recent_graduations" in dashboard["partners"]
    stages = [s["stage"] for s in dashboard["partners"]["funnel_stages"]]
    assert stages == [
        "registered",
        "profile_validated",
        "onboarding_complete",
        "recruitment_reviewed",
        "sandbox_close",
        "graduation_ready",
        "graduated",
    ]


def test_security_metrics_includes_partner_funnel(root):
    register_test_partner(root, agent_name=_unique_name())
    metrics = build_security_metrics(root)
    assert "partner_funnel" in metrics
    assert "test_partners" in metrics
    assert metrics["partner_funnel"]["registrations"] >= 1


def test_partners_me_progress_requires_sandbox_key(root):
    client = TestClient(create_app(root))
    resp = client.get("/partners/me/progress")
    assert resp.status_code == 401


def test_partners_me_progress_returns_journey(root):
    client = TestClient(create_app(root))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    sandbox_key = reg.json()["sandbox_key"]
    resp = client.get("/partners/me/progress", headers={"X-Arclya-Key": sandbox_key})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sandbox_mode"] is True
    assert data["milestone_progress"]["total"] == 6
    assert data["next_step"]["id"] == "validate_profile"


def test_validate_includes_partner_progress_with_sandbox_key(root):
    client = TestClient(create_app(root))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    sandbox_key = reg.json()["sandbox_key"]
    profile = {
        "agent_name": "Test Agent",
        "product_name": "Test Product",
        "product_description": "Agent-to-agent lead routing with pay-on-close tracking.",
        "target_customer": "SaaS agents",
        "typical_deal_size": "$49/mo",
        "common_objections": ["Price", "ROI", "Integration"],
        "preferred_pricing_model": "success_based",
        "accepts_crypto": False,
        "destination_link": "https://example.com/signup",
    }
    resp = client.post(
        "/onboarding/validate",
        json={"product_profile": profile},
        headers={"X-Arclya-Key": sandbox_key},
    )
    data = resp.json()
    assert data["valid"] is True
    assert data["partner_progress"]["milestones"]["profile_validated"] is True
    assert data["milestone_achieved"] == "profile_validated"
    assert data["next_step"]["id"] != "validate_profile"


def test_validate_includes_fix_hint_when_invalid(root):
    client = TestClient(create_app(root))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    sandbox_key = reg.json()["sandbox_key"]
    resp = client.post(
        "/onboarding/validate",
        json={"product_profile": {"agent_name": "X"}},
        headers={"X-Arclya-Key": sandbox_key},
    )
    data = resp.json()
    assert data["valid"] is False
    assert data["fields_remaining"] > 0
    assert "fix_hint" in data
    assert "partner_progress" in data


def _graduation_ready_partner(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    pid = partner["partner_id"]
    register_sandbox_key(root, partner_id=pid, agent_name=partner["agent_name"])
    record_partner_activity(root, pid, event="profile_validated")
    record_partner_activity(
        root,
        pid,
        event="handoff_complete",
        details={
            "summary": {
                "profile_saved": True,
                "onboarding_complete": True,
                "lead_routing_confirmed": True,
                "emergency_stop": False,
            }
        },
    )
    record_partner_activity(root, pid, event="recruitment_ready")
    return partner


def test_funnel_includes_graduated_stage(root):
    partner = _graduation_ready_partner(root)
    graduate_partner(root, partner_id=partner["partner_id"], graduated_by="ops_test")

    funnel = build_partner_funnel_metrics(root)
    assert funnel["graduated"] >= 1
    assert funnel["recent_graduations"]
    grad = funnel["recent_graduations"][0]
    assert grad["partner_id"] == partner["partner_id"]
    assert grad["graduated_by"] == "ops_test"
    assert grad["timestamp"]

    dashboard = build_ops_dashboard(root)
    text = format_ops_dashboard_text(dashboard)
    assert "Graduated:" in text
    assert "Recent graduations" in text
    assert partner["partner_id"] in text

    sec_text = format_security_dashboard_text(build_security_metrics(root))
    assert "Graduated:" in sec_text
    assert "Recent graduations" in sec_text


def test_partners_test_includes_funnel(root):
    client = TestClient(create_app(root))
    client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    resp = client.get("/partners/test")
    data = resp.json()
    assert "funnel" in data
    assert "stages" in data["funnel"]
    assert "success_definition" in data