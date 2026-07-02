"""Tests for custom-domain public URL resolution."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload


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


def test_agent_card_uses_arclya_public_url(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://agents.arclya.example")
    client = TestClient(create_app(isolated_root))
    card = client.get("/.well-known/agent-card.json").json()
    assert card["url"] == "https://agents.arclya.example"
    assert card["platform"]["public_url"] == "https://agents.arclya.example"
    assert card["platform"]["public_url_source"] == "ARCLYA_PUBLIC_URL"
    assert card["endpoints"]["agent_register"] == "https://agents.arclya.example/agents/register"


def test_registration_resources_use_public_url(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://agents.arclya.example")
    client = TestClient(create_app(isolated_root))
    resp = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name=f"Url_{uuid.uuid4().hex[:8]}",
            email=f"url_{uuid.uuid4().hex[:6]}@example.com",
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["resources"]["onboarding_guide"] == "https://agents.arclya.example/agents/onboarding/guide"
    assert data["resources"]["platform_status"] == "https://agents.arclya.example/status"
    assert data["terms"]["documentation_url"].startswith("https://agents.arclya.example/")


def test_status_includes_platform_summary(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://agents.arclya.example")
    client = TestClient(create_app(isolated_root))
    data = client.get("/status").json()
    summary = data["platform_summary"]
    assert summary["public_url"] == "https://agents.arclya.example"
    assert summary["public_url_source"] == "ARCLYA_PUBLIC_URL"
    assert data["status_page"] == "/platform/status"


def test_platform_status_html_page(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/platform/status")
    assert resp.status_code == 200
    assert "Platform Status" in resp.text
    assert "/agents/onboarding/guide" in resp.text
    assert "External agents" in resp.text