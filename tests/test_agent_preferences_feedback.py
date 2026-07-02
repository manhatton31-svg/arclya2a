"""Tests for agent preferences and structured feedback."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.audit import EVENT_FEEDBACK_SUBMITTED, EVENT_PREFERENCES_UPDATED, read_agent_audit_events
from arclya2a.agents.feedback import analyze_agent_feedback, list_agent_feedback
from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.agents.preferences import account_preferences, default_preferences
from arclya2a.learning.execution_analyzer import build_execution_learning_context
from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
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


def _register(client: TestClient) -> tuple[str, str]:
    resp = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name=f"Pref_{uuid.uuid4().hex[:8]}",
            email=f"pref_{uuid.uuid4().hex[:6]}@example.com",
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    return data["agent_id"], data["api_key"]


def test_registration_includes_default_preferences(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register(client)
    me = client.get("/agents/me", headers={"X-Arclya-Key": api_key}).json()
    assert me["preferences"] == default_preferences()


def test_patch_preferences(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, api_key = _register(client)
    headers = {"X-Arclya-Key": api_key}

    resp = client.patch(
        "/agents/me/preferences",
        headers=headers,
        json={"wants_human_closing": True, "preferred_closing_method": "hybrid"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preferences"]["wants_human_closing"] is True
    assert data["preferences"]["preferred_closing_method"] == "hybrid"
    assert "preferences_updated_at" in data

    events = read_agent_audit_events(isolated_root, event_type=EVENT_PREFERENCES_UPDATED, agent_id=agent_id)
    assert len(events) == 1


def test_patch_preferences_validation(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register(client)
    resp = client.patch(
        "/agents/me/preferences",
        headers={"X-Arclya-Key": api_key},
        json={"preferred_closing_method": "invalid"},
    )
    assert resp.status_code == 422


def test_submit_feedback(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, api_key = _register(client)
    headers = {"X-Arclya-Key": api_key}

    resp = client.post(
        "/agents/feedback",
        headers=headers,
        json={
            "category": "feature_request",
            "feature_interest": "human_closing",
            "wants_human_closing": True,
            "preferred_closing_method": "human_only",
            "message": "We need human-assisted closing for enterprise partners",
            "rating": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["submitted"] is True
    assert data["feedback_id"].startswith("afb_")
    assert data["learning_signal_id"].startswith("afs_")
    assert data["preferences"]["wants_human_closing"] is True
    assert data["preferences"]["preferred_closing_method"] == "human_only"

    feedback_rows = list_agent_feedback(isolated_root, agent_id=agent_id)
    assert len(feedback_rows) == 1

    events = read_agent_audit_events(isolated_root, event_type=EVENT_FEEDBACK_SUBMITTED, agent_id=agent_id)
    assert len(events) == 1


def test_feedback_requires_auth(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.post(
        "/agents/feedback",
        json={"category": "general", "message": "hello"},
    )
    assert resp.status_code == 401


def test_operator_feedback_endpoint(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-key")
    client = TestClient(create_app(isolated_root))
    _, api_key = _register(client)
    client.post(
        "/agents/feedback",
        headers={"X-Arclya-Key": api_key},
        json={
            "category": "closing_preference",
            "wants_human_closing": True,
            "message": "Prefer hybrid closes",
        },
    )

    denied = client.get("/agents/operator/feedback")
    assert denied.status_code == 401

    resp = client.get(
        "/agents/operator/feedback",
        headers={"X-Arclya-Operator-Key": "operator-test-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_feedback"] == 1
    assert len(body["entries"]) == 1


def test_ops_dashboard_includes_feedback(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register(client)
    client.patch(
        "/agents/me/preferences",
        headers={"X-Arclya-Key": api_key},
        json={"wants_human_closing": True, "preferred_closing_method": "hybrid"},
    )
    client.post(
        "/agents/feedback",
        headers={"X-Arclya-Key": api_key},
        json={"category": "general", "message": "Great platform"},
    )

    dashboard = build_ops_dashboard(isolated_root)
    assert "agent_feedback" in dashboard
    assert dashboard["agent_feedback"]["total_feedback"] == 1
    assert dashboard["agent_feedback"]["preferences"]["wants_human_closing_count"] == 1


def test_learning_context_includes_feedback(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register(client)
    client.post(
        "/agents/feedback",
        headers={"X-Arclya-Key": api_key},
        json={
            "category": "feature_request",
            "feature_interest": "human_closing",
            "wants_human_closing": True,
            "message": "Need human closing",
        },
    )
    client.post(
        "/agents/feedback",
        headers={"X-Arclya-Key": api_key},
        json={
            "category": "closing_preference",
            "wants_human_closing": True,
            "message": "Hybrid please",
        },
    )

    ctx = build_execution_learning_context(isolated_root)
    assert "agent_feedback" in ctx
    assert ctx["agent_feedback"]["human_closing_interest_count"] >= 2
    assert "agent_demand_human_closing" in ctx["issues_detected"]


def test_onboarding_guide_and_agent_card(isolated_root):
    client = TestClient(create_app(isolated_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == GUIDE_VERSION
    assert "preferences_and_feedback" in guide
    assert "PATCH /agents/me/preferences" in guide["preferences_and_feedback"]["preferences_endpoint"]

    card = client.get("/.well-known/agent-card.json").json()
    assert "agent_preferences" in card["platform"]["features"]
    assert "agent_feedback" in card["platform"]["features"]
    assert card["platform"]["agent_preferences"]["endpoint"] == "PATCH /agents/me/preferences"


def test_account_preferences_normalizes_legacy_accounts(isolated_root):
    account = {"agent_id": "ag_legacy000001"}
    prefs = account_preferences(account)
    assert prefs == default_preferences()