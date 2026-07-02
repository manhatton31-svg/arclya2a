"""Tests for external agent email verification."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.audit import EVENT_EMAIL_VERIFIED, read_agent_audit_events
from arclya2a.agents.email_verification import (
    _load_tokens,
    _write_tokens,
    latest_outbox_token,
    read_outbox_entries,
)
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list, verify_agent_from_outbox


def _unique_name() -> str:
    return f"Email_{uuid.uuid4().hex[:8]}"


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


def test_registration_sends_verification_email(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"reg_{uuid.uuid4().hex[:6]}@example.com"
    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["email_verified"] is False
    assert data["email_verification"]["sent"] is True
    assert data["email_verification"]["required_for_directory"] is True
    assert data["email_verification"]["delivery"] == "outbox"
    assert data["email_verification"]["status"]["email_verified"] is False

    outbox = read_outbox_entries(isolated_accounts_root)
    assert len(outbox) >= 1
    assert outbox[-1]["email"] == email
    assert outbox[-1]["token"].startswith("ev_")


def test_verify_email_sets_verified_flag(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"verify_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    agent_id = reg.json()["agent_id"]
    api_key = reg.json()["api_key"]

    verify_agent_from_outbox(client, isolated_accounts_root, agent_id=agent_id)

    me = client.get("/agents/me", headers={"X-Arclya-Key": api_key}).json()
    assert me["email_verified"] is True


def test_unverified_agent_cannot_join_directory(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"blocked_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    api_key = reg.json()["api_key"]

    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert resp.status_code == 422
    assert "email" in resp.json()["error"]["message"].lower()

    directory = client.get("/agents/directory").json()
    assert directory["total"] == 0


def test_verified_agent_can_join_directory(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id, api_key = register_verify_and_list(client, isolated_accounts_root)

    me = client.get("/agents/me", headers={"X-Arclya-Key": api_key}).json()
    assert me["email_verified"] is True
    assert me["publicly_listed"] is True

    directory = client.get("/agents/directory").json()
    assert directory["total"] == 1
    assert directory["agents"][0]["agent_id"] == agent_id


def test_verify_email_link_get(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"link_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    agent_id = reg.json()["agent_id"]
    token = latest_outbox_token(isolated_accounts_root, agent_id=agent_id)
    assert token

    resp = client.get("/agents/verify-email", params={"token": token})
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


def test_resend_verification_endpoint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"resend_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    api_key = reg.json()["api_key"]
    initial_count = len(read_outbox_entries(isolated_accounts_root))

    resp = client.post(
        "/agents/me/resend-verification",
        headers={"X-Arclya-Key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["resent"] is True
    assert len(read_outbox_entries(isolated_accounts_root)) > initial_count


def test_email_change_resets_verification(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email_a = f"a_{uuid.uuid4().hex[:6]}@example.com"
    email_b = f"b_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email_a),
    )
    api_key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]
    verify_agent_from_outbox(client, isolated_accounts_root, agent_id=agent_id)

    patch = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"email": email_b},
    )
    assert patch.status_code == 200
    assert patch.json()["profile"]["email_verified"] is False
    assert patch.json().get("email_verification", {}).get("sent") is True

    blocked = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert blocked.status_code == 422


def test_invalid_token_rejected(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post("/agents/verify-email", json={"token": "ev_invalid_token"})
    assert resp.status_code == 422


def test_expired_token_rejected(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"expired_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    agent_id = reg.json()["agent_id"]
    token = latest_outbox_token(isolated_accounts_root, agent_id=agent_id)
    assert token

    rows = _load_tokens(isolated_accounts_root)
    for row in rows:
        if row.get("token") == token:
            row["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_tokens(isolated_accounts_root, rows)

    resp = client.post("/agents/verify-email", json={"token": token})
    assert resp.status_code == 422
    assert "expired" in resp.json()["error"]["message"].lower()


def test_email_verification_audited(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    email = f"audit_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=email),
    )
    agent_id = reg.json()["agent_id"]
    verify_agent_from_outbox(client, isolated_accounts_root, agent_id=agent_id)

    events = read_agent_audit_events(
        isolated_accounts_root,
        agent_id=agent_id,
        event_type=EVENT_EMAIL_VERIFIED,
    )
    assert len(events) >= 1
    assert events[0]["details"]["email_verified"] is True


def test_registration_without_email_includes_verification_hint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post("/agents/register", json=registration_payload(agent_name=_unique_name()))
    assert resp.status_code == 200
    ev = resp.json().get("email_verification", {})
    assert ev.get("required_for_directory") is True
    assert ev.get("sent") is False


def test_agent_card_advertises_email_verification(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert "agent_email_verification" in card["platform"]["features"]
    ev = card["platform"]["agent_email_verification"]
    assert ev["required_for_directory"] is True
    assert ev["verify_endpoint"] == "POST /agents/verify-email"
    assert ev["resend_endpoint"] == "POST /agents/me/resend-verification"


def test_directory_requirement_disabled(isolated_accounts_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_REQUIRE_EMAIL_VERIFICATION", "false")
    client = TestClient(create_app(isolated_accounts_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=f"no_req_{uuid.uuid4().hex[:6]}@example.com"),
    )
    api_key = reg.json()["api_key"]

    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert resp.status_code == 200
    assert client.get("/agents/directory").json()["total"] == 1