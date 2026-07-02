"""Tests for external agent platform status in /health and /status."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.onboarding_guide import GUIDE_VERSION
from arclya2a.agents.platform_status import build_agent_platform_status
from arclya2a.agents.terms import current_terms_version
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, verify_agent_from_outbox


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


def test_build_agent_platform_status_empty(isolated_root):
    status = build_agent_platform_status(isolated_root)
    assert status["status"] == "available"
    assert status["onboarding_guide_version"] == GUIDE_VERSION
    assert status["terms_version"] == current_terms_version()
    assert status["accounts"]["total"] == 0
    assert status["documentation"]["production_readiness"] == "docs/production-readiness-checklist.md"


def test_platform_status_reflects_registered_agents(isolated_root):
    client = TestClient(create_app(isolated_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name=f"Status_{uuid.uuid4().hex[:8]}",
            email=f"status_{uuid.uuid4().hex[:6]}@example.com",
        ),
    )
    assert reg.status_code == 200
    verify_agent_from_outbox(client, isolated_root, agent_id=reg.json()["agent_id"])

    health = client.get("/health").json()
    assert health["external_agents"]["accounts_total"] == 1
    assert health["external_agents"]["registrations_24h"] >= 1

    full = client.get("/status").json()["external_agents"]
    assert full["accounts"]["total"] == 1
    assert full["accounts"]["active"] == 1
    assert full["activity_24h"]["registrations"] >= 1