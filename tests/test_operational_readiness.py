"""Tests for operational readiness: component health, status metrics, launch readiness."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.component_health import (
    build_component_health,
    build_crypto_component_health,
    build_email_component_health,
)
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


def test_email_health_outbox_dev_mode(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "outbox")
    email = build_email_component_health()
    assert email["delivery_mode_effective"] == "outbox"
    assert email["launch_ready"] is False
    assert email["status"] == "dev_mode"


def test_email_health_smtp_ready(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "auto")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://u:p@mail.example.com:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://agents.example.com")
    email = build_email_component_health()
    assert email["delivery_mode_effective"] == "smtp"
    assert email["launch_ready"] is True
    assert email["status"] == "healthy"
    assert email["public_url"] == "https://agents.example.com"


def test_crypto_health_disabled_by_default(isolated_root, monkeypatch):
    monkeypatch.delenv("ARCLYA_CRYPTO_ENABLED", raising=False)
    crypto = build_crypto_component_health(isolated_root)
    assert crypto["launch_ready"] is False
    assert crypto["status"] in {"disabled", "not_configured"}


def test_status_includes_component_health_and_launch_readiness(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "outbox")
    client = TestClient(create_app(isolated_root))
    data = client.get("/status").json()

    assert "component_health" in data
    assert "launch_readiness" in data
    assert data["component_health"]["email"]["delivery_mode_effective"] == "outbox"
    assert data["platform_summary"]["launch_ready"] is False
    assert "payments" in data["platform_summary"]
    assert "suspicious_events_24h" in data["platform_summary"]
    assert data["external_agents"]["activity_24h"]["suspicious_events"] == 0


def test_health_includes_component_summary(isolated_root):
    client = TestClient(create_app(isolated_root))
    health = client.get("/health").json()
    assert "components" in health
    assert "email" in health["components"]
    assert "crypto" in health["components"]
    assert "launch_ready" in health
    assert "email_delivery" in health
    assert "launch_next_steps" in health
    assert "suspicious_events_24h" in health["external_agents"]


def test_platform_status_html_shows_component_health(isolated_root):
    client = TestClient(create_app(isolated_root))
    resp = client.get("/platform/status")
    assert resp.status_code == 200
    assert "Component health" in resp.text
    assert "Email delivery" in resp.text
    assert "Crypto checkout" in resp.text


def test_status_reflects_registered_agent_metrics(isolated_root):
    client = TestClient(create_app(isolated_root))
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name=f"Ops_{uuid.uuid4().hex[:8]}",
            email=f"ops_{uuid.uuid4().hex[:6]}@example.com",
        ),
    )
    assert reg.status_code == 200

    summary = client.get("/status").json()["platform_summary"]
    assert summary["accounts_total"] >= 1
    assert summary["accounts_active"] >= 1
    assert summary["activity_24h"]["registrations"] >= 1


def test_build_component_health_aggregate(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "auto")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://resend:re_test@smtp.resend.com:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "onboarding@example.com")
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://agents.example.com")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x42387a2723fbd2ed52a4323d568ef501f55b6594")
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-key-32chars-minimum")

    health = build_component_health(isolated_root)
    assert health["email"]["launch_ready"] is True
    assert health["email"]["smtp_provider"] == "resend"
    assert health["crypto"]["launch_ready"] is True
    assert health["operator_key_configured"] is True
    assert health["launch_ready"] is True
    assert health["overall"] == "ready"
    assert health["next_steps"]