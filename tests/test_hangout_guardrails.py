"""Tests for constitutional guardrail enforcement in Agent Hangout."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.hangout_guardrails import find_handoff_chain_outcome, validate_hangout_guardrails
from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.audit.logger import append_audit_record
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, unique_agent_name, verify_agent_from_outbox

PRICING_MENU = {
    "version": "1.0.0",
    "currency": "USD",
    "margin_targets": {
        "minimum_percent": 15,
        "target_percent": 35,
        "veto_threshold_percent": 10,
    },
    "model_costs_per_1k_tokens": {
        "grok-3-mini": {"input": 0.0003, "output": 0.0005, "cached_input": 0.000075},
    },
    "service_tiers": {
        "outreach_sequence": {"base_price_usd": 49.0, "min_margin_percent": 20},
        "ai_closer_session": {"base_price_usd": 99.0, "min_margin_percent": 25},
    },
    "agent_overrides": {},
}


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
    (tmp_path / "pricing" / "pricing_menu.json").write_text(json.dumps(PRICING_MENU), encoding="utf-8")
    return tmp_path


def _register_agent(client: TestClient, root, *, name: str | None = None) -> tuple[str, str]:
    agent_name = name or unique_agent_name("Guardrail")
    email = f"guard_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(agent_name=agent_name, email=email, description="Guardrail tester"),
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    verify_agent_from_outbox(client, root, agent_id=data["agent_id"])
    return data["agent_id"], data["api_key"]


def _open_room(client: TestClient, api_key: str, partner_id: str | None = None) -> str:
    body: dict = {"title": "Guardrail deal", "topic": "routing", "capabilities": ["recruitment"]}
    if partner_id:
        body["invite_agent_ids"] = [partner_id]
    create = client.post("/agents/hangout/deal-rooms", headers={"X-Arclya-Key": api_key}, json=body)
    assert create.status_code == 200, create.text
    return create.json()["deal_room"]["room_id"]


def _seed_passed_orchestrator_run(root, *, deal_id: str) -> str:
    record = append_audit_record(
        root,
        agent_id="orchestrator",
        action="handoff_chain_complete",
        reasoning="test orchestrator run",
        metadata={
            "deal_id": deal_id,
            "agents_executed": ["closer", "profit_guardrail", "final_arbiter"],
            "emergency_stop": False,
            "outcome": {"margin_approved": True, "qc_passed": True, "deal_closed": True},
        },
    )
    return record["id"]


def test_deal_room_commitment_rejected_without_guardrails(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)
    room_id = _open_room(client, api_key)

    close = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": api_key},
        json={
            "close_type": "lead_routing_commitment",
            "lead_routing_confirmed": True,
            "confidence": 95,
        },
    )
    assert close.status_code == 422
    assert "guardrail" in close.json()["error"]["message"].lower() or "orchestrator" in close.json()["error"]["message"].lower()


def test_deal_room_commitment_passes_lightweight_guardrail(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)
    room_id = _open_room(client, api_key)

    msg = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/messages",
        headers={"X-Arclya-Key": api_key},
        json={"body": "Agreed on pay-on-close routing", "confidence": 90},
    )
    assert msg.status_code == 200

    close = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": api_key},
        json={
            "close_type": "lead_routing_commitment",
            "lead_routing_confirmed": True,
            "confidence": 95,
            "revenue_usd": 49.0,
            "cost_usd": 8.0,
        },
    )
    assert close.status_code == 200, close.text
    room = close.json()["deal_room"]
    assert room["status"] == "closed"
    assert room["constitutional_verification"]["passed"] is True
    assert room["constitutional_verification"]["method"] == "lightweight_check"


def test_deal_room_commitment_passes_orchestrator_run(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)
    room_id = _open_room(client, api_key)
    run_id = _seed_passed_orchestrator_run(isolated_root, deal_id=f"hangout_{room_id}")

    close = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": api_key},
        json={
            "close_type": "lead_routing_commitment",
            "lead_routing_confirmed": True,
            "confidence": 95,
            "handoff_run_id": run_id,
        },
    )
    assert close.status_code == 200, close.text
    verification = close.json()["deal_room"]["constitutional_verification"]
    assert verification["passed"] is True
    assert verification["method"] == "orchestrator_run"


def test_exploratory_close_skips_guardrails(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)
    room_id = _open_room(client, api_key)

    close = client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": api_key},
        json={"close_type": "exploratory", "lead_routing_confirmed": False, "confidence": 90},
    )
    assert close.status_code == 200
    assert close.json()["deal_room"].get("constitutional_verification") is None


def test_paid_marketplace_complete_requires_guardrails(isolated_root):
    client = TestClient(create_app(isolated_root))
    _, api_key = _register_agent(client, isolated_root)

    listing = client.post(
        "/agents/hangout/marketplace",
        headers={"X-Arclya-Key": api_key},
        json={
            "listing_type": "offer",
            "title": "Paid routing offer",
            "description": "Warm leads with margin-positive terms",
            "price_usd": 40.0,
        },
    )
    assert listing.status_code == 200
    listing_id = listing.json()["listing"]["listing_id"]

    blocked = client.post(
        f"/agents/hangout/marketplace/{listing_id}/complete",
        headers={"X-Arclya-Key": api_key},
        json={"revenue_usd": 40.0, "cost_usd": 35.0},
    )
    assert blocked.status_code == 422

    ok = client.post(
        f"/agents/hangout/marketplace/{listing_id}/complete",
        headers={"X-Arclya-Key": api_key},
        json={"revenue_usd": 40.0, "cost_usd": 5.0},
    )
    assert ok.status_code == 200, ok.text
    row = ok.json()["listing"]
    assert row["constitutional_verification"]["passed"] is True


def test_reputation_counts_constitutional_closes(isolated_root):
    client = TestClient(create_app(isolated_root))
    agent_id, api_key = _register_agent(client, isolated_root)
    room_id = _open_room(client, api_key)

    client.post(
        f"/agents/hangout/deal-rooms/{room_id}/messages",
        headers={"X-Arclya-Key": api_key},
        json={"body": "Negotiated terms", "confidence": 88},
    )
    client.post(
        f"/agents/hangout/deal-rooms/{room_id}/close",
        headers={"X-Arclya-Key": api_key},
        json={
            "close_type": "lead_routing_commitment",
            "lead_routing_confirmed": True,
            "confidence": 95,
            "revenue_usd": 50.0,
            "cost_usd": 10.0,
        },
    )

    rep = client.get(f"/agents/{agent_id}/reputation").json()
    assert rep["constitutional_deal_room_closes"] == 1
    assert rep["constitutional_close_count"] >= 1
    assert "constitutional_deal_room_closes" in rep["factors"] or rep["deal_room_closes"] == 1


def test_onboarding_guide_documents_hangout_guardrails(isolated_root):
    client = TestClient(create_app(isolated_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == GUIDE_VERSION
    enforcement = guide["agent_hangout"]["constitutional"]["guardrail_enforcement"]
    assert "deal_room_commitment" in enforcement
    assert guide["agent_hangout"]["deal_rooms"]["close"]["constitutional_required_for_commitment"] is True


def test_find_handoff_chain_outcome_by_deal_id(isolated_root):
    deal_id = "deal_guardrail_test"
    append_audit_record(
        isolated_root,
        agent_id="orchestrator",
        action="handoff_chain_complete",
        reasoning="test",
        metadata={
            "deal_id": deal_id,
            "emergency_stop": False,
            "outcome": {"margin_approved": True, "qc_passed": True},
        },
    )
    found = find_handoff_chain_outcome(isolated_root, deal_id=deal_id)
    assert found is not None
    result = validate_hangout_guardrails(
        isolated_root,
        agent_id="ag_test",
        deal_id=deal_id,
        message_count=1,
        close_confidence=90.0,
    )
    assert result.passed is True
    assert result.method == "orchestrator_run"