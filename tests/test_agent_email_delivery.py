"""Tests for production SMTP email delivery and outbox fallback."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.email_delivery import (
    deliver_plaintext_email,
    effective_email_delivery_mode,
    parse_smtp_url,
    send_smtp_message,
)
from arclya2a.agents.email_verification import (
    build_verification_email_content,
    read_outbox_entries,
    send_verification_email,
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


def test_parse_smtp_url():
    cfg = parse_smtp_url("smtp://user%40x.com:secret@mail.example.com:587")
    assert cfg["host"] == "mail.example.com"
    assert cfg["port"] == 587
    assert cfg["username"] == "user@x.com"
    assert cfg["password"] == "secret"
    assert cfg["use_ssl"] is False


def test_outbox_mode_ignores_smtp_url(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "outbox")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://u:p@localhost:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@example.com")
    assert effective_email_delivery_mode() == "outbox"

    account = {"agent_id": "ag_test", "agent_name": "Test", "email": "a@example.com"}
    result = send_verification_email(
        isolated_root,
        account=account,
        token="ev_testtoken",
        base_url="https://agents.example.com",
    )
    assert result["delivery"] == "outbox"
    assert result["sent"] is True
    assert "verify_link" in result
    assert "agents.example.com" in result["verify_link"]

    outbox = read_outbox_entries(isolated_root)[-1]
    assert outbox["public_base_url"] == "https://agents.example.com"
    assert "agents.example.com/agents/verify-email" in outbox["verify_link"]


@patch("arclya2a.agents.email_delivery.smtplib.SMTP")
def test_smtp_delivery_sends_email(mock_smtp, isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "smtp")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://user:pass@localhost:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@arclya.example")

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    result = deliver_plaintext_email(
        to="agent@example.com",
        subject="Test",
        body="Hello",
    )
    assert result["delivery"] == "smtp"
    assert result["sent"] is True
    smtp_instance.send_message.assert_called_once()


def test_registration_smtp_mode_via_http(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "smtp")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://user:pass@localhost:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@arclya.example")
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://launch.arclya.example")

    with patch("arclya2a.agents.email_delivery.smtplib.SMTP") as mock_smtp:
        smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = smtp_instance

        client = TestClient(create_app(isolated_root))
        resp = client.post(
            "/agents/register",
            json=registration_payload(
                agent_name=f"Smtp_{uuid.uuid4().hex[:8]}",
                email=f"smtp_{uuid.uuid4().hex[:6]}@example.com",
            ),
        )
        assert resp.status_code == 200
        ev = resp.json()["email_verification"]
        assert ev["delivery"] == "smtp"
        assert ev["sent"] is True
        assert ev["delivery_mode"] == "smtp"
        assert "verify_link" not in ev
        assert ev["status"]["directory_ready"] is False

        outbox = read_outbox_entries(isolated_root)[-1]
        assert outbox["delivery"] == "smtp"
        assert "launch.arclya.example" in outbox["verify_link"]
        smtp_instance.send_message.assert_called_once()


def test_verification_email_includes_clean_link(isolated_root):
    plain, html = build_verification_email_content(
        agent_name="Test Agent",
        verify_link="https://agents.example.com/agents/verify-email?token=ev_abc",
        token="ev_abc",
        base_url="https://agents.example.com",
        hours=24,
    )
    assert "https://agents.example.com/agents/verify-email?token=ev_abc" in plain
    assert "Verify email" in html
    assert "ev_abc" in plain


@patch("arclya2a.agents.email_delivery.smtplib.SMTP")
def test_smtp_sends_multipart_html(mock_smtp, isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "smtp")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://user:pass@localhost:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@arclya.example")

    smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__.return_value = smtp_instance

    account = {"agent_id": "ag_html", "agent_name": "Html", "email": "html@example.com"}
    send_verification_email(
        isolated_root,
        account=account,
        token="ev_htmltoken",
        base_url="https://launch.arclya.example",
    )
    sent = smtp_instance.send_message.call_args[0][0]
    assert sent.get_content_type() == "multipart/alternative"


def test_smtp_failure_returns_sent_false(isolated_root, monkeypatch):
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_DELIVERY", "smtp")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_SMTP_URL", "smtp://user:pass@localhost:587")
    monkeypatch.setenv("ARCLYA_AGENT_EMAIL_FROM", "noreply@arclya.example")

    with patch("arclya2a.agents.email_delivery.send_smtp_message", side_effect=OSError("connection refused")):
        account = {"agent_id": "ag_x", "agent_name": "Fail", "email": "fail@example.com"}
        result = send_verification_email(
            isolated_root,
            account=account,
            token="ev_fail",
            base_url="https://arclya.example",
        )
        assert result["sent"] is False
        assert result["smtp_error"]
        outbox = read_outbox_entries(isolated_root)[-1]
        assert outbox.get("smtp_error")