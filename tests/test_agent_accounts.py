"""Tests for external agent account registration and profiles."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.accounts import (
    get_agent_account,
    lookup_agent_by_api_key,
    register_agent_account,
)
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload


def _unique_name() -> str:
    return f"ExtAgent_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    """Use an isolated data directory for agent account tests."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "pricing").mkdir()

    import json

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


def test_register_agent_account_persists_and_issues_prod_key(isolated_accounts_root):
    account, api_key, err = register_agent_account(
        isolated_accounts_root,
        agent_name="Research Bot",
        email="bot@example.com",
        description="Finds warm leads",
        capabilities=["lead_research", "outreach"],
        terms_accepted=True,
    )
    assert err is None
    assert account is not None
    assert api_key.startswith("arclya_prod_")
    assert account["agent_id"].startswith("ag_")
    assert account["status"] == "active"

    stored = get_agent_account(isolated_accounts_root, account["agent_id"])
    assert stored is not None
    assert stored["agent_name"] == "Research Bot"
    assert stored["email"] == "bot@example.com"
    assert stored["capabilities"] == ["lead_research", "outreach"]

    resolved = lookup_agent_by_api_key(isolated_accounts_root, api_key)
    assert resolved is not None
    assert resolved["agent_id"] == account["agent_id"]


def test_agents_register_endpoint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name= _unique_name(),
            email= "agent@example.com",
            description= "External recruiting agent",
            capabilities= ["recruitment", "a2a_handoff"])
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_key"].startswith("arclya_prod_")
    assert data["agent_id"].startswith("ag_")
    assert data["status"] == "active"
    assert data["profile"]["capabilities"] == ["recruitment", "a2a_handoff"]
    assert data.get("registered") is True
    assert "welcome_message" in data
    assert data["api_key_reminder"]["shown_once"] is True


def test_agents_register_requires_agent_name(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post("/agents/register", json={"description": "missing name"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_agents_me_requires_api_key(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "authentication_error"


def test_agents_me_returns_profile_with_api_key(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name= _unique_name(),
            email= "me@example.com",
            bio= "My agent bio",
            capabilities= ["onboarding"])
    )
    api_key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]

    resp = client.get("/agents/me", headers={"X-Arclya-Key": api_key})
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == agent_id
    assert data["email"] == "me@example.com"
    assert data["description"] == "My agent bio"
    assert data["capabilities"] == ["onboarding"]
    assert data["api_key_prefix"].startswith("arclya_prod_")


def test_agents_me_update_profile(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), description="Original bio"),
    )
    api_key = reg.json()["api_key"]
    headers = {"X-Arclya-Key": api_key}

    resp = client.patch(
        "/agents/me",
        headers=headers,
        json={
            "description": "Updated bio",
            "capabilities": ["closing", "objection_handling"],
        },
    )
    assert resp.status_code == 200
    profile = resp.json()["profile"]
    assert profile["description"] == "Updated bio"
    assert profile["capabilities"] == ["closing", "objection_handling"]

    me = client.get("/agents/me", headers=headers).json()
    assert me["description"] == "Updated bio"


def test_agents_public_profile(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name= _unique_name(),
            email= "public@example.com",
            description= "Public-facing agent",
            capabilities= ["discovery"])
    )
    agent_id = reg.json()["agent_id"]

    resp = client.get(f"/agents/{agent_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == agent_id
    assert data["agent_name"]
    assert data["description"] == "Public-facing agent"
    assert data["capabilities"] == ["discovery"]
    assert data["capability_count"] == 1
    assert data["created_at"]
    assert data["updated_at"]
    assert data["status"] == "active"
    assert data["account_type"] == "external_agent"
    assert data["profile_url"].endswith(f"/agents/{agent_id}")
    assert data["has_email"] is True
    assert "email" not in data
    assert "api_key" not in data
    assert "api_key_prefix" not in data


def test_agent_card_includes_account_endpoints(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert "agent_account_registration" in card["platform"]["features"]
    assert card["endpoints"]["agent_register"].endswith("/agents/register")
    assert card["endpoints"]["agent_profile"].endswith("/agents/me")
    doc_rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "agent-register" in doc_rels
    assert "agent-profile" in doc_rels