"""Edge-case and hardening tests for agent accounts and directory."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.accounts import (
    DESCRIPTION_MAX_LEN,
    register_agent_account,
    update_agent_profile,
    validate_capabilities,
)
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list


def _unique_name() -> str:
    return f"Harden_{uuid.uuid4().hex[:8]}"


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


def test_registration_returns_welcome_payload(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), description="Test agent"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["registered"] is True
    assert "welcome_message" in data
    assert "api_key_reminder" in data
    assert "resources" in data
    assert "what_you_get" in data
    assert "next_steps" in data
    assert len(data["next_steps"]) >= 8
    assert data["onboarding_guide_url"] == "http://testserver/agents/onboarding/guide"
    assert data["api_key_reminder"]["shown_once"] is True


def test_registration_field_errors_for_missing_name(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post("/agents/register", json={"email": "a@b.com"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["fields"][0]["field"] == "agent_name"


def test_registration_rejects_duplicate_email(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"dup_{uuid.uuid4().hex[:6]}@example.com"
    assert client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    ).status_code == 200

    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    assert resp.status_code == 422
    fields = resp.json()["error"]["details"]["fields"]
    assert any(f["field"] == "email" and "already exists" in f["message"] for f in fields)


def test_registration_rejects_invalid_capabilities_type(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), capabilities="recruitment"),
    )
    assert resp.status_code == 422
    fields = resp.json()["error"]["details"]["fields"]
    assert fields[0]["field"] == "capabilities"


def test_registration_rejects_oversized_description(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), description="x" * (DESCRIPTION_MAX_LEN + 1)),
    )
    assert resp.status_code == 422
    fields = resp.json()["error"]["details"]["fields"]
    assert fields[0]["field"] == "description"


def test_capabilities_deduplicated_on_register(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name= _unique_name(),
            capabilities= ["Recruitment", "recruitment", "closing"])
    )
    assert resp.status_code == 200
    assert resp.json()["profile"]["capabilities"] == ["Recruitment", "closing"]


def test_patch_rejects_duplicate_email(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email_a = f"a_{uuid.uuid4().hex[:6]}@example.com"
    email_b = f"b_{uuid.uuid4().hex[:6]}@example.com"
    key_a = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email_a),
    ).json()["api_key"]
    client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email_b),
    )

    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": key_a},
        json={"email": email_b},
    )
    assert resp.status_code == 422
    assert "already exists" in resp.json()["error"]["message"]


def test_patch_rejects_empty_body(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    key = client.post("/agents/register", json=registration_payload(agent_name=_unique_name())).json()["api_key"]
    resp = client.patch("/agents/me", headers={"X-Arclya-Key": key}, json={})
    assert resp.status_code == 422
    assert "At least one profile field" in resp.json()["error"]["message"]


def test_patch_rejects_non_boolean_publicly_listed(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    key = client.post("/agents/register", json=registration_payload(agent_name=_unique_name())).json()["api_key"]
    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": key},
        json={"publicly_listed": "yes"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["field"] == "publicly_listed"


def test_patch_listing_note_on_opt_in(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, key = register_verify_and_list(client, isolated_accounts_root)
    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": key},
        json={"publicly_listed": True},
    )
    assert resp.status_code == 200
    assert "visible" in resp.json()["listing_note"]


def test_invalid_agent_id_format_returns_404(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/not-a-valid-id")
    assert resp.status_code == 404
    assert resp.json()["error"]["details"]["hint"]


def test_onboarding_guide_endpoint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/onboarding/guide")
    assert resp.status_code == 200
    guide = resp.json()
    assert guide["title"]
    assert guide["version"]
    assert guide["post_registration"] is not None
    assert len(guide["full_flow"]["steps"]) >= 8
    assert guide["directory"]["opt_in_field"] == "publicly_listed"
    assert "recruitment" in guide["suggested_capabilities"]


def test_directory_invalid_sort_falls_back(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, key = register_verify_and_list(client, isolated_accounts_root)

    resp = client.get("/agents", params={"sort": "invalid_sort"})
    assert resp.status_code == 200
    assert resp.json()["pagination"]["sort"] == "created_at_desc"
    assert "sort_fallback" in resp.json()["pagination"]


def test_validate_capabilities_module_messages():
    ok, err, caps = validate_capabilities(["ok", ""])
    assert not ok
    assert "capabilities[1]" in err

    ok, err, caps = validate_capabilities("bad")
    assert not ok
    assert "JSON array" in err


def test_update_normalizes_email(isolated_accounts_root):
    account, _, _ = register_agent_account(
        isolated_accounts_root,
        agent_name="Email Test",
        email="Original@Example.COM",
        terms_accepted=True,
    )
    updated, err = update_agent_profile(
        isolated_accounts_root,
        account["agent_id"],
        email="Updated@Example.COM",
    )
    assert err is None
    assert updated["email"] == "updated@example.com"