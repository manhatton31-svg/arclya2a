"""Tests for Terms of Service / Acceptable Use Policy acceptance."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.audit import EVENT_TERMS_ACCEPTED, read_agent_audit_events
from arclya2a.agents.terms import CURRENT_TERMS_VERSION, current_terms_version
from arclya2a.server.app import create_app
from tests.agent_helpers import register_verify_and_list, registration_payload, verify_agent_from_outbox


def _unique_name() -> str:
    return f"Terms_{uuid.uuid4().hex[:8]}"


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


def test_registration_accepts_accept_terms_alias(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json={
            "agent_name": _unique_name(),
            "email": f"alias_{uuid.uuid4().hex[:6]}@example.com",
            "accept_terms": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["terms_accepted"] is True
    assert data["terms_version"] == CURRENT_TERMS_VERSION
    assert data["profile"]["terms_accepted_at"]


def test_registration_requires_terms_acceptance(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json={"agent_name": _unique_name()},
    )
    assert resp.status_code == 422
    fields = resp.json()["error"]["details"]["fields"]
    assert any(f["field"] == "terms_accepted" for f in fields)


def test_registration_records_terms_on_success(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name(), email=f"t_{uuid.uuid4().hex[:6]}@example.com"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["terms_accepted"] is True
    assert data["terms_version"] == CURRENT_TERMS_VERSION
    assert data["profile"]["terms_accepted"] is True
    assert "terms" in data

    events = read_agent_audit_events(
        isolated_accounts_root,
        agent_id=data["agent_id"],
        event_type=EVENT_TERMS_ACCEPTED,
    )
    assert len(events) >= 1


def test_terms_metadata_endpoint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/terms")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == current_terms_version()
    assert data["required_at_registration"] is True
    assert data["required_for_directory"] is True
    assert "documentation" in data
    assert data["terms_of_service"] == "docs/terms-of-service.md"
    assert data["acceptable_use_policy"] == "docs/acceptable-use-policy.md"
    assert "accept_terms" in data["accept_field_aliases"]


def test_unverified_terms_blocks_directory_without_acceptance(isolated_accounts_root, monkeypatch):
    """Agent registered before terms (simulated) cannot opt into directory."""
    from arclya2a.agents.accounts import register_agent_account

    account, api_key, err = register_agent_account(
        isolated_accounts_root,
        agent_name=_unique_name(),
        email=f"old_{uuid.uuid4().hex[:6]}@example.com",
        terms_accepted=True,
    )
    assert err is None
    # Simulate legacy account without terms
    from arclya2a.agents.accounts import _load_all, _write_all

    rows = _load_all(isolated_accounts_root)
    for row in rows:
        if row["agent_id"] == account["agent_id"]:
            row["terms_version"] = None
            row["terms_accepted_at"] = None
    _write_all(isolated_accounts_root, rows)

    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert resp.status_code == 422
    assert "terms" in resp.json()["error"]["message"].lower()


def test_patch_accept_terms_enables_directory(isolated_accounts_root):
    from arclya2a.agents.accounts import _load_all, _write_all, register_agent_account

    account, api_key, _ = register_agent_account(
        isolated_accounts_root,
        agent_name=_unique_name(),
        email=f"patch_{uuid.uuid4().hex[:6]}@example.com",
        terms_accepted=True,
    )
    rows = _load_all(isolated_accounts_root)
    for row in rows:
        if row["agent_id"] == account["agent_id"]:
            row["terms_version"] = None
            row["terms_accepted_at"] = None
    _write_all(isolated_accounts_root, rows)

    client = TestClient(create_app(isolated_accounts_root, api_key="platform-secret"))
    accept = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"terms_accepted": True},
    )
    assert accept.status_code == 200
    assert accept.json()["profile"]["terms_accepted"] is True
    assert accept.json()["profile"]["terms_version"] == CURRENT_TERMS_VERSION

    from arclya2a.agents.email_verification import issue_verification_token, verify_email_token

    token_record = issue_verification_token(
        isolated_accounts_root,
        agent_id=account["agent_id"],
        email=account["email"],
    )
    verified, err = verify_email_token(isolated_accounts_root, token_record["token"])
    assert err is None
    assert verified["email_verified"] is True

    listed = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert listed.status_code == 200


def test_directory_listing_requires_terms(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, key = register_verify_and_list(client, isolated_accounts_root)
    assert client.get("/agents/directory").json()["total"] == 1


def test_patch_rejects_terms_revocation(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    key = client.post(
        "/agents/register",
        json=registration_payload(agent_name=_unique_name()),
    ).json()["api_key"]
    resp = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": key},
        json={"terms_accepted": False},
    )
    assert resp.status_code == 422
    assert "cannot be revoked" in resp.json()["error"]["message"].lower()


def test_onboarding_guide_documents_terms(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == "1.5.1"
    assert guide["terms"]["version"] == CURRENT_TERMS_VERSION
    assert guide["directory"]["requires_terms_accepted"] is True
    assert "production_readiness" in guide
    assert guide["production_readiness"]["checklist"] == "docs/production-readiness-checklist.md"


def test_agent_card_advertises_terms(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert "agent_terms_acceptance" in card["platform"]["features"]
    terms = card["platform"]["agent_terms_acceptance"]
    assert terms["current_version"] == CURRENT_TERMS_VERSION
    assert terms["metadata_endpoint"] == "GET /agents/terms"
    assert terms["terms_of_service"] == "docs/terms-of-service.md"
    assert terms["acceptable_use_policy"] == "docs/acceptable-use-policy.md"
    assert "accept_terms" in terms["accept_field_aliases"]
    assert card["endpoints"]["agent_terms"].endswith("/agents/terms")