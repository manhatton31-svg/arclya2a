"""Tests for machine-readable agent service catalog and discoverability."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.capability_discovery import expand_capability
from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.agents.service_catalog import build_service_catalog
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, verify_agent_from_outbox

ROOT = Path(__file__).resolve().parents[1]


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
        (ROOT / "agents" / "registry.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "pricing" / "agent_payment_packages.json").write_text(
        (ROOT / "pricing" / "agent_payment_packages.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def test_service_catalog_endpoint(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/agents/services")
    assert resp.status_code == 200
    data = resp.json()
    assert data["catalog_type"] == "machine_readable"
    assert data["service_count"] >= 5
    assert "constitutional_guarantees" in data["platform"]
    assert data["discovery"]["agent_card"].endswith("/.well-known/agent-card.json")
    ids = {s["id"] for s in data["services"]}
    assert "a2a_closing" in ids
    assert "partner_recruitment" in ids


def test_discovery_services_alias(isolated_root):
    client = TestClient(create_app(isolated_root))
    primary = client.get("/agents/services").json()
    alias = client.get("/discovery/services").json()
    assert primary["service_count"] == alias["service_count"]
    assert primary["platform"]["name"] == alias["platform"]["name"]


def test_service_catalog_capability_filter(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/agents/services", params={"capability": "closer"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["filters_applied"]["capability"] == "closer"
    assert any(s["id"] == "a2a_closing" for s in data["services"])


def test_agent_card_discoverability_enhancements(isolated_root):
    client = TestClient(create_app(isolated_root))
    card = client.get("/.well-known/agent-card.json").json()
    platform = card["platform"]
    assert "service_catalog" in platform
    assert platform["service_catalog"]["endpoint"] == "GET /agents/services"
    assert "discoverability" in platform
    assert "reputation_platform" in platform
    assert "capability_synonyms" in platform["agent_directory_capabilities"]
    assert card["a2a"]["compliance"]["signed_agent_card"] is True
    assert card["a2a"]["payments"]["x402_version"] == 2
    assert card["endpoints"]["agent_service_catalog"].endswith("/agents/services")
    doc_rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "agent-service-catalog" in doc_rels
    closer_skill = next(s for s in card["skills"] if s["id"] == "closer")
    assert closer_skill.get("success_metrics")


def test_onboarding_guide_for_autonomous_agents(isolated_root):
    client = TestClient(create_app(isolated_root))
    guide = client.get("/agents/onboarding/guide").json()
    assert guide["version"] == GUIDE_VERSION
    assert "for_autonomous_agents" in guide
    assert "closer" in guide["for_autonomous_agents"]["capability_search_hints"]
    assert "service_catalog" in guide["resources"]


def test_directory_capability_synonym_closer(isolated_root):
    client = TestClient(create_app(isolated_root))
    email = f"close_{uuid.uuid4().hex[:6]}@example.com"
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name=f"Closer_{uuid.uuid4().hex[:6]}",
            email=email,
            description="A2A closing specialist",
            capabilities=["a2a_closing"],
        ),
    )
    assert reg.status_code == 200
    api_key = reg.json()["api_key"]
    agent_id = reg.json()["agent_id"]
    verify_agent_from_outbox(client, isolated_root, agent_id=agent_id)
    client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )

    resp = client.get("/agents/directory", params={"capability": "closer"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    assert resp.json()["agents"][0]["capabilities"] == ["a2a_closing"]


def test_expand_capability_closer():
    expanded = expand_capability("closer")
    assert "a2a_closing" in expanded
    assert "closing" in expanded


def test_build_service_catalog_module(isolated_root):
    catalog = build_service_catalog(isolated_root, base_url="https://arclya.example")
    assert catalog["platform"]["constitutional_guarantees"]["qc_gate"] == "final_arbiter"
    assert catalog["quick_start_for_agents"][0]["action"].startswith("GET")