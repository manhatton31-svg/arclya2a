"""Tests for sandbox mode and test partner onboarding."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.orchestrator.agent_runner import _validate_closer
from arclya2a.partners.onboarding_guide import build_onboarding_guide
from arclya2a.audit.logger import read_audit_records
from arclya2a.partners.sandbox import (
    SANDBOX_BLOCKED_TOOLS,
    apply_sandbox_markers,
    is_sandbox_active,
    is_sandbox_path_blocked,
    is_sandbox_tool_blocked,
    register_sandbox_key,
    sandbox_rate_limit,
    set_sandbox_active,
    validate_agent_card_url,
)
from arclya2a.partners.test_registry import apply_security_event
from arclya2a.partners.test_registry import (
    GRADUATION_CRITERIA,
    list_test_partners,
    record_partner_activity,
    register_test_partner,
)
from arclya2a.server.app import create_app
from arclya2a.tools.executor import execute_tool_requests


def _unique_name() -> str:
    return f"TestAgent_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def relax_sandbox_register_limits(monkeypatch):
    """Prevent cross-test IP registration limits on shared testclient host."""
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_REGISTER_PER_IP_DAY", "1000")
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_KEYS_PER_AGENT", "10")


def test_sandbox_register_returns_key_and_guide(root):
    client = TestClient(create_app(root))
    resp = client.post(
        "/partners/sandbox/register",
        json={
            "agent_name": _unique_name(),
            "agent_card_url": "https://example.com/.well-known/agent-card.json",
            "target_customer": "SaaS founders",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sandbox_key"].startswith("arclya_sandbox_")
    assert data["mode"] == "sandbox"
    assert data["tools_mode"] == "dry_run"
    assert data["billing"] == "disabled"
    assert data["partner_id"].startswith("tp_")
    assert len(data["next_steps"]) >= 1
    assert data["guide_url"] == "/partners/onboarding/guide"


def test_sandbox_register_requires_agent_name(root):
    client = TestClient(create_app(root))
    resp = client.post("/partners/sandbox/register", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_sandbox_register_rejects_invalid_agent_card_url(root):
    client = TestClient(create_app(root))
    resp = client.post(
        "/partners/sandbox/register",
        json={"agent_name": _unique_name(), "agent_card_url": "http://insecure.example/card"},
    )
    assert resp.status_code == 422
    assert "HTTPS" in resp.json()["error"]["message"]


def test_sandbox_register_limits_keys_per_agent_name(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_KEYS_PER_AGENT", "1")
    client = TestClient(create_app(root))
    name = _unique_name()
    assert client.post("/partners/sandbox/register", json={"agent_name": name}).status_code == 200
    resp = client.post("/partners/sandbox/register", json={"agent_name": name})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "registration_denied"


def test_sandbox_blocks_high_risk_paths(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="prod-secret"))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    sandbox_key = reg.json()["sandbox_key"]
    resp = client.post(
        "/learning/run",
        json={},
        headers={"X-Arclya-Key": sandbox_key},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sandbox_forbidden"


def test_sandbox_blocks_high_risk_tools_even_in_dry_run(root, monkeypatch):
    monkeypatch.delenv("ARCLYA_TOOL_DRY_RUN", raising=False)
    set_sandbox_active(True)
    try:
        results = execute_tool_requests(
            root,
            "closer",
            [{"tool_id": "gmail.send_followup_email", "parameters": {"to": "a@b.com", "subject": "x", "body": "y"}}],
        )
        assert len(results) == 1
        assert results[0]["skipped"] is True
        assert results[0]["error_code"] == "SANDBOX_HIGH_RISK_TOOL"
        assert not results[0].get("dry_run")
    finally:
        set_sandbox_active(False)


def test_emergency_stop_blocks_graduation(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    pid = partner["partner_id"]
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
    apply_security_event(root, pid, event_type="emergency_stop")
    partner = get_partner(root, pid)
    assert partner["milestones"]["no_emergency_stops"] is False
    assert partner["graduation_ready"] is False


def test_sandbox_registration_writes_audit_record(root):
    client = TestClient(create_app(root))
    before = len(read_audit_records(root, limit=500))
    client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    after = read_audit_records(root, limit=500)
    sandbox_actions = [r for r in after if r.get("action", "").startswith("sandbox_")]
    assert len(sandbox_actions) > before or any(r["action"] == "sandbox_registered" for r in after)


def test_validate_agent_card_url_helpers():
    assert validate_agent_card_url(None)[0] is True
    assert validate_agent_card_url("https://agent.example/.well-known/agent-card.json")[0] is True
    assert validate_agent_card_url("ftp://bad.example")[0] is False
    assert is_sandbox_tool_blocked("gmail.send_followup_email")
    assert "gmail.send_followup_email" in SANDBOX_BLOCKED_TOOLS
    assert is_sandbox_path_blocked("/billing/deals")


def test_onboarding_guide_endpoint(root):
    client = TestClient(create_app(root))
    resp = client.get("/partners/onboarding/guide")
    assert resp.status_code == 200
    guide = resp.json()
    assert guide["mode"] == "sandbox"
    assert len(guide["steps"]) == 5
    assert guide["graduation_criteria"] == GRADUATION_CRITERIA
    assert guide["progress_endpoint"] == "GET /partners/me/progress"


def test_test_partners_list_endpoint(root):
    name = _unique_name()
    client = TestClient(create_app(root))
    reg = client.post("/partners/sandbox/register", json={"agent_name": name})
    assert reg.status_code == 200
    partner_id = reg.json()["partner_id"]

    resp = client.get("/partners/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    ids = {p["partner_id"] for p in data["partners"]}
    assert partner_id in ids
    partner = next(p for p in data["partners"] if p["partner_id"] == partner_id)
    assert partner["agent_name"] == name
    assert partner["status"] == "sandbox"
    assert "sandbox_key" not in partner


def test_sandbox_key_auth_and_mode_header(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="prod-secret"))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    sandbox_key = reg.json()["sandbox_key"]

    resp = client.post(
        "/orchestrate/handoff-chain",
        json={
            "deal_id": "sandbox_auth_test",
            "customer_company": "Sandbox Co",
            "task_context": "Sandbox auth test",
            "auto_route": False,
            "entry_agent": "outreach_worker",
        },
        headers={"X-Arclya-Key": sandbox_key, "X-Arclya-Agent-Id": "sandbox_agent"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Arclya-Mode") == "sandbox"
    data = resp.json()
    assert data.get("sandbox_mode") is True
    assert "test_marker" in data


def test_sandbox_handoff_records_partner_activity(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="prod-secret"))
    reg = client.post("/partners/sandbox/register", json={"agent_name": _unique_name()})
    partner_id = reg.json()["partner_id"]
    sandbox_key = reg.json()["sandbox_key"]

    client.post(
        "/orchestrate/handoff-chain",
        json={
            "deal_id": "sandbox_activity_test",
            "customer_company": "Activity Co",
            "task_context": "Activity tracking",
            "auto_route": False,
            "entry_agent": "outreach_worker",
        },
        headers={"X-Arclya-Key": sandbox_key},
    )

    partners = list_test_partners(root)
    partner = next(p for p in partners if p["partner_id"] == partner_id)
    assert partner["handoff_count"] >= 1
    assert partner["last_seen_at"] is not None


def test_sandbox_tools_dry_run_by_default(root, monkeypatch):
    monkeypatch.delenv("ARCLYA_TOOL_DRY_RUN", raising=False)
    set_sandbox_active(True)
    try:
        results = execute_tool_requests(
            root,
            "closer",
            [
                {
                    "tool_id": "linear.create_followup_task",
                    "reason": "Sandbox test",
                    "parameters": {"title": "Test", "description": "Sandbox"},
                },
            ],
        )
        assert len(results) == 1
        assert results[0].get("dry_run") is True
    finally:
        set_sandbox_active(False)
    assert is_sandbox_active() is False


def test_sandbox_billing_skipped_in_closer_validator(root):
    agent = {"id": "closer", "handoff_targets": ["profit_guardrail"]}
    profile = {
        "agent_name": "Sandbox Seller",
        "product_name": "Sandbox Product",
        "product_description": "A sandbox product for testing lead routing closes.",
        "target_customer": "Test buyers",
        "typical_deal_size": "$10",
        "common_objections": ["A", "B", "C"],
        "preferred_pricing_model": "success_based",
        "accepts_crypto": False,
        "destination_link": "https://example.com/go",
        "affiliate_code": "SBX1",
    }
    handoff = {
        "status": "COMPLETE",
        "payload": {
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "close_package": {"cta_url": "https://example.com/go?ref=SBX1"},
        },
    }
    context = {
        "ssot": {
            "deal_id": "sandbox_bill",
            "metadata": {
                "product_profile": profile,
                "product_profile_complete": True,
                "onboarding_complete": True,
            },
        },
        "revenue_usd": 100,
        "estimated_cost_usd": 10,
    }

    set_sandbox_active(True)
    try:
        result = _validate_closer(agent, handoff, root, context)
        billing = result["payload"]["billing_record"]
        assert billing["sandbox"] is True
        assert billing["skipped"] is True
    finally:
        set_sandbox_active(False)


def test_apply_sandbox_markers():
    payload = {"valid": True}
    marked = apply_sandbox_markers(payload, sandbox=True)
    assert marked["sandbox_mode"] is True
    assert marked["tools_mode"] == "dry_run"
    unmarked = apply_sandbox_markers(payload, sandbox=False)
    assert "sandbox_mode" not in unmarked


def test_registry_milestones_and_graduation(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    pid = partner["partner_id"]
    register_sandbox_key(root, partner_id=pid, agent_name=partner["agent_name"])

    updated = record_partner_activity(
        root,
        pid,
        event="profile_validated",
    )
    assert updated["milestones"]["profile_validated"] is True

    updated = record_partner_activity(
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
    assert updated["milestones"]["onboarding_complete"] is True
    assert updated["milestones"]["close_dry_run"] is True

    record_partner_activity(root, pid, event="recruitment_ready")
    updated = get_partner(root, pid)
    assert updated["milestones"]["recruitment_reviewed"] is True
    assert updated["graduation_ready"] is True
    assert updated["status"] == "graduation_ready"


def get_partner(root, partner_id: str):
    for row in list_test_partners(root, limit=200):
        if row["partner_id"] == partner_id:
            return row
    pytest.fail(f"partner {partner_id} not found")


def test_build_onboarding_guide_structure():
    guide = build_onboarding_guide()
    assert guide["steps"][0]["id"] == "register"
    assert guide["sandbox_defaults"]["tools"] == "dry_run"
    assert sandbox_rate_limit() >= 3
    assert sandbox_rate_limit() <= 60