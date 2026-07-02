"""Tests for external agent audit logging."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.audit import (
    EVENT_AGENT_REGISTERED,
    EVENT_AUTH_FAILURE,
    EVENT_DIRECTORY_OPT_IN,
    EVENT_DIRECTORY_SEARCH,
    EVENT_PROFILE_UPDATED,
    agent_audit_path,
    build_agent_audit_summary,
    read_agent_audit_events,
)
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list, verify_agent_from_outbox


def _unique_name() -> str:
    return f"Audit_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "pricing").mkdir()

    (tmp_path / "config" / "core.json").write_text(
        json.dumps(
            {
                "platform_name": "Arclya A2A",
                "version": "0.1.0",
                "server": {"host": "127.0.0.1", "port": 8787, "base_url": "http://127.0.0.1:8787"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "registry.json").write_text(
        json.dumps({"version": "1.0.0", "agents": []}),
        encoding="utf-8",
    )
    return tmp_path


def test_registration_creates_audit_event(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    assert resp.status_code == 200
    agent_id = resp.json()["agent_id"]

    assert agent_audit_path(isolated_accounts_root).exists()
    events = read_agent_audit_events(isolated_accounts_root, agent_id=agent_id)
    assert any(e["event_type"] == EVENT_AGENT_REGISTERED for e in events)
    registered = next(e for e in events if e["event_type"] == EVENT_AGENT_REGISTERED)
    assert registered["agent_id"] == agent_id
    assert "agent_name" in registered["details"]


def test_profile_update_and_opt_in_audited(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id, key = register_verify_and_list(
        client,
        isolated_accounts_root,
        description="Audit test agent",
    )
    headers = {"X-Arclya-Key": key}

    resp = client.patch(
        "/agents/me",
        headers=headers,
        json={"description": "Updated description"}
    )
    assert resp.status_code == 200

    events = read_agent_audit_events(isolated_accounts_root, agent_id=agent_id)
    assert any(e["event_type"] == EVENT_PROFILE_UPDATED for e in events)
    assert any(e["event_type"] == EVENT_DIRECTORY_OPT_IN for e in events)
    profile_evt = next(e for e in events if e["event_type"] == EVENT_PROFILE_UPDATED)
    assert "description" in profile_evt["details"]["changed_fields"]


def test_directory_search_audited(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, key = register_verify_and_list(client, isolated_accounts_root)

    client.get("/agents", params={"q": "audit"})
    events = read_agent_audit_events(isolated_accounts_root, event_type=EVENT_DIRECTORY_SEARCH)
    assert len(events) >= 1
    assert events[0]["details"]["mode"] == "search"


def test_auth_failure_audited(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    client.get("/agents/me")
    events = read_agent_audit_events(isolated_accounts_root, event_type=EVENT_AUTH_FAILURE)
    assert len(events) >= 1
    assert events[0]["details"]["reason"] == "missing_api_key"


def test_agents_audit_endpoint_requires_operator(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/audit")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "operator_authentication_error"


def test_agents_audit_endpoint_returns_events(isolated_accounts_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-key")
    client = TestClient(create_app(isolated_accounts_root))
    client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))

    resp = client.get(
        "/agents/audit",
        headers={"X-Arclya-Operator-Key": "operator-test-key"},
        params={"limit": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert "summary" in data
    assert data["summary"]["audit_log"] == "data/audit/agent_actions.jsonl"


def test_ops_dashboard_includes_agent_audit(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    dash = client.get("/ops/dashboard").json()
    assert "agents" in dash
    assert dash["agents"]["total_events"] >= 1
    assert "recent_events" in dash["agents"]


def test_agent_card_advertises_audit(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get("/.well-known/agent-card.json").json()
    audit = card["platform"]["agent_action_audit"]
    assert audit["enabled"] is True
    assert audit["operator_endpoint"] == "GET /agents/audit"
    assert "agent_action_audit" in card["platform"]["features"]


def test_suspicious_directory_activity_flagged(isolated_accounts_root, monkeypatch):
    monkeypatch.setattr("arclya2a.agents.audit.SUSPICIOUS_IP_DIRECTORY_THRESHOLD", 2)
    client = TestClient(
        create_app(isolated_accounts_root, agent_directory_rate_limit_per_minute=100)
    )
    for _ in range(3):
        client.get("/agents", params={"q": "probe"})
    events = read_agent_audit_events(
        isolated_accounts_root,
        event_type=EVENT_DIRECTORY_SEARCH,
        suspicious_only=True,
    )
    assert len(events) >= 1
    assert events[0]["suspicious"] is True


def test_build_agent_audit_summary_module(isolated_accounts_root):
    from arclya2a.agents.audit import log_agent_audit

    log_agent_audit(
        isolated_accounts_root,
        event_type=EVENT_AGENT_REGISTERED,
        agent_id="ag_test12345678",
        client_ip="127.0.0.1",
        details={"agent_name": "Test"}
    )
    summary = build_agent_audit_summary(isolated_accounts_root)
    assert summary["total_events"] >= 1
    assert summary["counts_24h"]["agent_registered"] >= 1