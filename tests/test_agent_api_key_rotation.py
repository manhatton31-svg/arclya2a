"""Tests for external agent API key rotation."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.audit import EVENT_API_KEY_ROTATED, read_agent_audit_events
from arclya2a.partners.production_keys import lookup_production_key
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload


OPERATOR_KEY = "rotate-test-operator-key"


def _unique_name() -> str:
    return f"Rotate_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", OPERATOR_KEY)
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


@pytest.fixture
def operator_headers():
    return {"X-Arclya-Operator-Key": OPERATOR_KEY}


def test_rotate_key_returns_new_key_and_revokes_old(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    reg = client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    assert reg.status_code == 200
    old_key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]

    resp = client.post(
        "/agents/me/rotate-key",
        headers={"X-Arclya-Key": old_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rotated"] is True
    assert data["api_key"].startswith("arclya_prod_")
    assert data["api_key"] != old_key
    assert data["api_key_reminder"]["shown_once"] is True

    new_key = data["api_key"]
    assert lookup_production_key(isolated_accounts_root, old_key) is None
    assert lookup_production_key(isolated_accounts_root, new_key) is not None

    assert client.get("/agents/me", headers={"X-Arclya-Key": old_key}).status_code == 401
    me = client.get("/agents/me", headers={"X-Arclya-Key": new_key})
    assert me.status_code == 200
    assert me.json()["agent_id"] == agent_id


def test_rotate_key_requires_authentication(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    resp = client.post("/agents/me/rotate-key")
    assert resp.status_code == 401


def test_rotate_key_rate_limited(isolated_accounts_root):
    client = TestClient(
        create_app(
            isolated_accounts_root,
            api_key="platform-secret",
            agent_rotate_key_rate_limit_per_minute=2,
        )
    )
    key = client.post("/agents/register", json=registration_payload(agent_name=_unique_name())).json()["api_key"]
    headers = {"X-Arclya-Key": key}

    for _ in range(2):
        rotate = client.post("/agents/me/rotate-key", headers=headers)
        assert rotate.status_code == 200
        key = rotate.json()["api_key"]
        headers = {"X-Arclya-Key": key}

    resp = client.post("/agents/me/rotate-key", headers=headers)
    assert resp.status_code == 429
    assert resp.json()["error"]["details"]["bucket"] == "rotate_key"


def test_rotate_key_audited(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    reg = client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]

    client.post("/agents/me/rotate-key", headers={"X-Arclya-Key": key})

    events = read_agent_audit_events(
        isolated_accounts_root,
        agent_id=agent_id,
        event_type=EVENT_API_KEY_ROTATED,
    )
    assert len(events) >= 1
    assert events[0]["details"]["rotated_by"] == "agent"
    assert events[0]["details"]["revoked_key_prefixes"]


def test_operator_force_rotate_key(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    reg = client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    old_key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]

    resp = client.post(
        f"/agents/{agent_id}/rotate-key",
        headers=operator_headers,
        json={"reason": "lost key recovery"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rotated"] is True
    assert data["api_key"].startswith("arclya_prod_")
    assert data["reason"] == "lost key recovery"

    assert lookup_production_key(isolated_accounts_root, old_key) is None
    assert client.get("/agents/me", headers={"X-Arclya-Key": data["api_key"]}).status_code == 200

    events = read_agent_audit_events(
        isolated_accounts_root,
        agent_id=agent_id,
        event_type=EVENT_API_KEY_ROTATED,
    )
    assert any(e["details"]["rotated_by"] == "operator" for e in events)


def test_operator_rotate_requires_operator(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id = client.post("/agents/register", json=registration_payload(agent_name=_unique_name())).json()["agent_id"]
    resp = client.post(f"/agents/{agent_id}/rotate-key")
    assert resp.status_code == 401


def test_registration_mentions_key_rotation_recovery(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    data = client.post("/agents/register", json=registration_payload(agent_name=_unique_name())).json()
    assert "rotate-key" in data["api_key_reminder"]["recovery"]["compromised"]
    store_step = next(s for s in data["next_steps"] if s["id"] == "store_api_key")
    assert "rotate" in store_step["description"].lower()


def test_onboarding_guide_documents_key_rotation(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == "1.5.1"
    assert guide["api_key_rotation"]["self_service_endpoint"] == "POST /agents/me/rotate-key"
    assert guide["authentication"]["rotate_endpoint"] == "POST /agents/me/rotate-key"


def test_agent_card_advertises_key_rotation(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert "agent_api_key_rotation" in card["platform"]["features"]
    rotation = card["platform"]["agent_api_key_rotation"]
    assert rotation["self_service_endpoint"] == "POST /agents/me/rotate-key"
    assert rotation["operator_endpoint"] == "POST /agents/{agent_id}/rotate-key"
    assert "POST /agents/me/rotate-key" in card["platform"]["agent_endpoint_security"]["rate_limits_per_minute"]
    assert card["endpoints"]["agent_rotate_key"].endswith("/agents/me/rotate-key")
    assert card["platform"]["agent_operator_management"]["endpoints"]["rotate_key"] == (
        "POST /agents/{agent_id}/rotate-key"
    )