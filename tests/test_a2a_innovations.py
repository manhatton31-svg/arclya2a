"""Tests for 5 A2A/x402 innovations + Agent Referral Program."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.agent_identity import verify_signature
from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.agents.referrals import referral_code_for_agent
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, unique_agent_name, verify_agent_from_outbox


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


def _register(client, root, **fields) -> tuple[str, str]:
    reg = client.post("/agents/register", json=registration_payload(**fields))
    assert reg.status_code == 200, reg.text
    data = reg.json()
    return data["agent_id"], data["api_key"]


def test_signed_platform_agent_card(isolated_root):
    client = TestClient(create_app(isolated_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert "signature" in card
    assert card["signature"]["algorithm"] == "HS256"
    assert card["a2a"]["protocol_version"] == "1.0"
    assert card["a2a"]["signed_agent_card"] is True

    sig = card.pop("signature")
    assert verify_signature(card, sig, root=isolated_root)

    verify_resp = client.post("/.well-known/agent-card/verify", json={**card, "signature": sig})
    assert verify_resp.json()["valid"] is True


def test_per_agent_signed_agent_card(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, _ = _register(client, isolated_root, agent_name=unique_agent_name("Card"))
    card = client.get(f"/agents/{agent_id}/agent-card.json").json()
    assert card["signature"]["algorithm"] == "HS256"
    assert card["a2a"]["identity"]["did"] == f"did:arclya:{agent_id}"


def test_x402_v2_facilitators(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/payments/crypto/x402/facilitators")
    if resp.status_code == 503:
        pytest.skip("crypto not configured in test env")
    data = resp.json()
    assert data["x402Version"] == 2
    ids = {f["id"] for f in data["facilitators"]}
    assert "arclya-batch" in ids
    assert "arclya-deferred" in ids


def test_reputation_directory_sort(isolated_root):
    client = TestClient(create_app(isolated_root))
    low_id, low_key = _register(
        client,
        isolated_root,
        agent_name=unique_agent_name("Low"),
        email=f"low_{uuid.uuid4().hex[:6]}@example.com",
        description="Minimal agent profile",
        capabilities=["onboarding"],
    )
    high_id, high_key = _register(
        client,
        isolated_root,
        agent_name=unique_agent_name("High"),
        email=f"high_{uuid.uuid4().hex[:6]}@example.com",
        description="Experienced recruiting agent with strong track record",
        capabilities=["recruitment", "closing"],
    )
    for aid, key in ((low_id, low_key), (high_id, high_key)):
        verify_agent_from_outbox(client, isolated_root, agent_id=aid)
        client.patch("/agents/me", headers={"X-Arclya-Key": key}, json={"publicly_listed": True})

    ranked = client.get("/agents/directory", params={"sort": "trust_score_desc"}).json()
    assert ranked["pagination"]["sort"] == "trust_score_desc"
    if ranked["count"] >= 2:
        assert ranked["agents"][0]["reputation"]["trust_score"] >= ranked["agents"][1]["reputation"]["trust_score"]


def test_reputation_guardrail_strictness_in_api(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, _ = _register(client, isolated_root, agent_name=unique_agent_name("Rep"))
    rep = client.get(f"/agents/{agent_id}/reputation").json()
    assert "guardrail_strictness" in rep
    assert rep["guardrail_strictness"]["min_close_confidence"] >= 82.0


def test_agent_referral_program_flow(isolated_root):
    client = TestClient(create_app(isolated_root))
    referrer_id, referrer_key = _register(
        client,
        isolated_root,
        agent_name=unique_agent_name("Referrer"),
        email=f"ref_{uuid.uuid4().hex[:6]}@example.com",
        description="Referrer agent profile",
        capabilities=["recruitment"],
    )
    program = client.get("/agents/referrals/program").json()
    assert program["enabled"] is True
    assert program["reward_currency"] == "USDC"

    code_resp = client.get("/agents/me/referral-code", headers={"X-Arclya-Key": referrer_key})
    assert code_resp.status_code == 200
    ref_code = code_resp.json()["referral_code"]
    assert ref_code == referral_code_for_agent(referrer_id)

    referred_id, referred_key = _register(
        client,
        isolated_root,
        agent_name=unique_agent_name("Referred"),
        email=f"referred_{uuid.uuid4().hex[:6]}@example.com",
        description="Referred agent completing onboarding",
        capabilities=["onboarding"],
        referral_code=ref_code,
    )
    verify_agent_from_outbox(client, isolated_root, agent_id=referred_id)
    pending = client.get("/agents/me/referrals", headers={"X-Arclya-Key": referrer_key}).json()
    assert pending["stats"]["total_referrals"] >= 1
    assert pending["stats"]["pending_onboarding"] >= 1

    client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": referred_key},
        json={"publicly_listed": True},
    )
    dashboard = client.get("/agents/me/referrals", headers={"X-Arclya-Key": referrer_key}).json()
    assert dashboard["stats"]["completed"] >= 1

    invite = client.post(
        "/agents/referrals/invite",
        headers={"X-Arclya-Key": referrer_key},
        json={"invitee_name": "Prospect"},
    )
    assert invite.status_code == 200
    assert invite.json()["invitation"]["referral_code"] == ref_code

    me = client.get("/agents/me", headers={"X-Arclya-Key": referrer_key}).json()
    assert "referral_program" in me
    assert me["referral_program"]["referral_code"] == ref_code


def test_onboarding_guide_advertises_innovations(isolated_root):
    client = TestClient(create_app(isolated_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == GUIDE_VERSION
    assert "innovations" in guide
    assert "agent_referral_program" in guide
    assert guide["innovations"]["signed_agent_cards"]["a2a_protocol_version"] == "1.0"


def test_agent_card_advertises_all_innovations(isolated_root):
    client = TestClient(create_app(isolated_root))
    card = client.get("/.well-known/agent-card.json").json()
    features = card["platform"]["features"]
    for feat in (
        "signed_agent_cards",
        "x402_v2_native",
        "agent_referral_program",
        "deal_room_micropayments",
        "reputation_directory_ranking",
    ):
        assert feat in features
    assert card["platform"]["x402_v2"]["version"] == 2
    assert card["platform"]["agent_referral_program"]["discovery"]