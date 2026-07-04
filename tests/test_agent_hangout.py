"""Tests for Agent Hangout: deal rooms, hubs, marketplace, reputation, A2A discovery."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, unique_agent_name, verify_agent_from_outbox


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "pricing").mkdir()
    (tmp_path / "pricing" / "pricing_menu.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "currency": "USD",
                "margin_targets": {
                    "minimum_percent": 15,
                    "target_percent": 35,
                    "veto_threshold_percent": 10,
                },
                "model_costs_per_1k_tokens": {},
                "service_tiers": {
                    "outreach_sequence": {"base_price_usd": 49.0, "min_margin_percent": 20},
                },
                "agent_overrides": {},
            }
        ),
        encoding="utf-8",
    )
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


def _register_agent(client: TestClient, root, *, name: str | None = None) -> tuple[str, str]:
    agent_name = name or unique_agent_name("Hangout")
    email = f"hangout_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=agent_name, email=email, description="Hangout tester"),
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    verify_agent_from_outbox(client, root, agent_id=data["agent_id"])
    return data["agent_id"], data["api_key"]


def test_hangout_discovery(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/agents/hangout")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Arclya Agent Hangout"
    assert data["constitutional"]["inference"] == "xai_only"
    assert data["constitutional"]["qc_gate"] == "final_arbiter"
    assert "guardrail_enforcement" in data["constitutional"]
    assert data["handoff_signals"]["task_delegation"] is True
    assert data["payments"]["currency"] == "USDC"
    assert data["payments"]["x402_compatible"] is True
    assert "deal_rooms" in data["endpoints"]


def test_agent_card_a2a_and_hangout(isolated_root):
    client = TestClient(create_app(isolated_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert card["a2a"]["inference"]["xai_only"] is True
    assert card["a2a"]["payments"]["x402_compatible"] is True
    assert "agent_hangout" in card["platform"]["features"]
    assert "deal_rooms" in card["platform"]["features"]
    assert "reputation_trust_scoring" in card["platform"]["features"]
    assert card["endpoints"]["agent_hangout"].endswith("/agents/hangout")
    doc_rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "agent-hangout" in doc_rels
    assert "agent-reputation" in doc_rels


def test_onboarding_guide_includes_hangout(isolated_root):
    client = TestClient(create_app(isolated_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == GUIDE_VERSION
    assert "agent_hangout" in guide
    assert guide["agent_hangout"]["deal_rooms"]["close"]["close_type_default"] == "lead_routing_commitment"
    assert guide["resources"]["agent_hangout"].endswith("/agents/hangout")
    hangout_step = next(s for s in guide["post_registration"]["steps"] if s["id"] == "join_hangout")
    assert hangout_step["method"] == "GET"


def test_deal_room_lifecycle(isolated_root):
    client = TestClient(create_app(isolated_root))
    host_id, host_key = _register_agent(client, isolated_root)
    partner_id, _ = _register_agent(client, isolated_root, name=unique_agent_name("Partner"))

    create = client.post(
        "/agents/hangout/deal-rooms",
        headers={"X-Arclya-Key": host_key},
        json={
            "title": "SaaS routing deal",
            "topic": "recruitment",
            "capabilities": ["recruitment"],
            "invite_agent_ids": [partner_id],
        },
    )
    assert create.status_code == 200, create.text
    room = create.json()["deal_room"]
    room_id = room["room_id"]
    assert room_id.startswith("dr_")
    assert room["status"] == "open"
    assert partner_id in room["participants"]

    unauth = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/messages",
        json={"body": "Should fail"},
    )
    assert unauth.status_code == 401

    msg = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/messages",
        headers={"X-Arclya-Key": host_key},
        json={"body": "Proposing lead routing commitment", "confidence": 88},
    )
    assert msg.status_code == 200
    assert msg.json()["message"]["confidence"] == 88

    detail = client.get(f"/agents/hangout/deal-rooms/{room_id}").json()["deal_room"]
    assert detail["message_count"] == 1

    close = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": host_key},
        json={
            "close_type": "lead_routing_commitment",
            "lead_routing_confirmed": True,
            "confidence": 95,
            "revenue_usd": 49.0,
            "cost_usd": 8.0,
        },
    )
    assert close.status_code == 200
    closed = close.json()["deal_room"]
    assert closed["status"] == "closed"
    assert closed["lead_routing_confirmed"] is True
    assert closed["constitutional_verification"]["passed"] is True

    mine = client.get(
        "/agents/hangout/deal-rooms",
        headers={"X-Arclya-Key": host_key},
        params={"mine": True},
    ).json()
    assert mine["count"] >= 1


def test_collaboration_hub_create_and_search(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)

    join = client.post(
        "/agents/hangout/hubs",
        headers={"X-Arclya-Key": api_key},
        json={
            "topic": "saas-partners",
            "capability": "recruitment",
            "vertical": "b2b",
            "description": "SaaS affiliate recruitment hangout",
        },
    )
    assert join.status_code == 200, join.text
    hub = join.json()["hub"]
    assert hub["hub_id"].startswith("hub_")
    assert hub["member_count"] == 1

    by_cap = client.get("/agents/hangout/hubs", params={"capability": "recruitment"}).json()
    assert by_cap["count"] >= 1
    assert by_cap["hubs"][0]["topic"] == "saas-partners"

    by_q = client.get("/agents/hangout/hubs", params={"q": "affiliate"}).json()
    assert by_q["count"] >= 1

    # Second agent joins same topic+capability hub
    _, key2 = _register_agent(client, isolated_root, name=unique_agent_name("HubJoin"))
    join2 = client.post(
        "/agents/hangout/hubs",
        headers={"X-Arclya-Key": key2},
        json={"topic": "saas-partners", "capability": "recruitment"},
    )
    assert join2.status_code == 200
    assert join2.json()["hub"]["hub_id"] == hub["hub_id"]
    assert join2.json()["hub"]["member_count"] == 2


def test_marketplace_offer_and_anti_duplication(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)

    listing = client.post(
        "/agents/hangout/marketplace",
        headers={"X-Arclya-Key": api_key},
        json={
            "listing_type": "offer",
            "title": "Recruitment outreach",
            "description": "Warm lead routing for SaaS sellers",
            "capabilities": ["recruitment"],
            "price_usd": 25.0,
        },
    )
    assert listing.status_code == 200, listing.text
    row = listing.json()["listing"]
    listing_id = row["listing_id"]
    assert listing_id.startswith("mp_")
    assert row["payment"]["currency"] == "USDC"
    assert row["payment"]["x402_compatible"] is True

    dup = client.post(
        "/agents/hangout/marketplace",
        headers={"X-Arclya-Key": api_key},
        json={
            "listing_type": "offer",
            "title": "Recruitment outreach",
            "description": "Different description but same title",
            "capabilities": ["recruitment"],
        },
    )
    assert dup.status_code == 422

    browse = client.get("/agents/hangout/marketplace", params={"listing_type": "offer"}).json()
    assert browse["count"] >= 1
    assert browse["currency"] == "USDC"

    checkout = client.get(f"/agents/hangout/marketplace/{listing_id}/checkout").json()
    assert checkout["x402_compatible"] is True
    assert "checkout" in checkout
    assert checkout["checkout"]["currency"] == "USDC"

    complete = client.post(
        f"/agents/hangout/marketplace/{listing_id}/complete",
        headers={"X-Arclya-Key": api_key},
        json={"revenue_usd": 25.0, "cost_usd": 3.0},
    )
    assert complete.status_code == 200
    completed = complete.json()["listing"]
    assert completed["status"] == "completed"
    assert completed["constitutional_verification"]["passed"] is True


def test_reputation_score_and_profile_integration(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, api_key = _register_agent(client, isolated_root)

    rep = client.get(f"/agents/{agent_id}/reputation").json()
    assert rep["found"] is True
    assert 0 <= rep["trust_score"] <= 100
    assert rep["trust_tier"] in {"new", "building", "established", "trusted"}

    listed = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert listed.status_code == 200

    rep2 = client.get(f"/agents/{agent_id}/reputation").json()
    assert rep2["trust_score"] >= rep["trust_score"]

    profile = client.get(f"/agents/{agent_id}").json()
    assert "reputation" in profile
    assert profile["reputation"]["trust_score"] == rep2["trust_score"]

    directory = client.get("/agents/directory").json()
    if directory["count"]:
        assert "reputation" in directory["agents"][0]


def test_hangout_requires_auth_for_mutations(isolated_root):
    client = TestClient(create_app(isolated_root))
    for path, body in [
        ("/agents/hangout/deal-rooms", {"title": "x", "topic": "y"}),
        ("/agents/hangout/hubs", {"topic": "z"}),
        ("/agents/hangout/marketplace", {"listing_type": "offer", "title": "a", "description": "b"}),
    ]:
        resp = client.post(path, json=body)
        assert resp.status_code == 401, path