"""Tests for operator partner graduation workflow."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.audit.logger import read_audit_records
from arclya2a.partners.graduation import (
    GraduationError,
    assess_graduation_readiness,
    graduate_partner,
    resolve_partner_identifier,
)
from arclya2a.partners.production_keys import lookup_production_key
from arclya2a.partners.sandbox import load_sandbox_keys, register_sandbox_key
from arclya2a.partners.test_registry import record_partner_activity, register_test_partner
from arclya2a.server.app import create_app


def _unique_name() -> str:
    return f"GradAgent_{uuid.uuid4().hex[:8]}"


def _graduation_ready_partner(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    pid = partner["partner_id"]
    sandbox_key = register_sandbox_key(root, partner_id=pid, agent_name=partner["agent_name"])
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
    return partner, sandbox_key


@pytest.fixture
def operator_env(monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")


def test_assess_graduation_readiness_blocks_incomplete(root):
    partner = register_test_partner(root, agent_name=_unique_name())
    assessment = assess_graduation_readiness(root, partner["partner_id"])
    assert assessment["ready"] is False
    assert assessment["graduation_ready"] is False
    assert assessment["reasons"]


def test_graduate_partner_success(root, operator_env):
    partner, sandbox_key = _graduation_ready_partner(root)
    pid = partner["partner_id"]

    result = graduate_partner(root, partner_id=pid, graduated_by="test_operator")

    assert result["success"] is True
    assert result["production_key"].startswith("arclya_prod_")
    assert len(result["sandbox_keys_revoked"]) >= 1

    keys = load_sandbox_keys(root)
    for key, entry in keys.items():
        if entry.get("partner_id") == pid:
            assert entry.get("active") is False

    assert lookup_production_key(root, result["production_key"]) is not None

    audit = read_audit_records(root, limit=50)
    assert any(r.get("action") == "partner_graduated" for r in audit)

    log_path = root / "data" / "test_partners" / "graduation_log.jsonl"
    assert log_path.exists()
    assert pid in log_path.read_text(encoding="utf-8")

    assessment = assess_graduation_readiness(root, pid)
    assert assessment["ready"] is False
    assert "already graduated" in " ".join(assessment["reasons"]).lower()


def test_graduate_partner_blocked_when_not_ready(root, operator_env):
    partner = register_test_partner(root, agent_name=_unique_name())
    with pytest.raises(GraduationError) as exc_info:
        graduate_partner(root, partner_id=partner["partner_id"], graduated_by="test_operator")
    assert exc_info.value.code == "graduation_blocked"
    assert exc_info.value.reasons


def test_resolve_partner_identifier_from_sandbox_key(root):
    partner, sandbox_key = _graduation_ready_partner(root)
    resolved = resolve_partner_identifier(root, sandbox_key=sandbox_key)
    assert resolved == partner["partner_id"]


def test_graduate_api_endpoint(root, operator_env):
    partner, sandbox_key = _graduation_ready_partner(root)
    client = TestClient(create_app(root, api_key="prod-secret"))

    blocked = client.post("/partners/graduate", json={"partner_id": partner["partner_id"]})
    assert blocked.status_code == 401

    resp = client.post(
        "/partners/graduate",
        json={"sandbox_key": sandbox_key, "performed_by": "api_operator"},
        headers={"X-Arclya-Operator-Key": "operator-test-secret-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["production_key"].startswith("arclya_prod_")
    assert data["sandbox_keys_revoked"]


def test_graduate_api_blocked_when_not_ready(root, operator_env):
    partner = register_test_partner(root, agent_name=_unique_name())
    client = TestClient(create_app(root))
    resp = client.post(
        "/partners/graduate",
        json={"partner_id": partner["partner_id"]},
        headers={"X-Arclya-Operator-Key": "operator-test-secret-key"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "graduation_blocked"
    assert body["error"]["details"]["blocking_reasons"]


def test_production_key_authenticates(root, operator_env, mock_xai):
    partner, _ = _graduation_ready_partner(root)
    result = graduate_partner(root, partner_id=partner["partner_id"], graduated_by="auth_test")
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="global-prod-key"))

    denied = client.post(
        "/orchestrate/handoff-chain",
        json={
            "deal_id": "grad_auth_test",
            "customer_company": "Grad Co",
            "task_context": "Production key auth test",
            "auto_route": False,
            "entry_agent": "outreach_worker",
        },
    )
    assert denied.status_code == 401

    ok = client.post(
        "/orchestrate/handoff-chain",
        json={
            "deal_id": "grad_auth_test",
            "customer_company": "Grad Co",
            "task_context": "Production key auth test",
            "auto_route": False,
            "entry_agent": "outreach_worker",
        },
        headers={"X-Arclya-Key": result["production_key"]},
    )
    assert ok.status_code == 200
    assert ok.headers.get("X-Arclya-Mode") != "sandbox"