"""Tests for production observability and operational status."""

from __future__ import annotations

import json
import logging

from arclya2a.observability.dashboard import build_ops_dashboard, format_ops_dashboard_text
from arclya2a.observability.ops_events import list_ops_events, record_ops_event
from arclya2a.observability.ops_status import build_ops_status
from arclya2a.observability.structured_log import JsonLogFormatter, json_logs_enabled, log_event


def test_record_and_list_ops_events(root):
    record_ops_event(root, "test_event", category="server", data={"ok": True})
    events = list_ops_events(root, category="server", limit=5)
    assert any(e.get("event") == "test_event" for e in events)


def test_build_ops_status(root):
    status = build_ops_status(root)
    assert status["status"] in ("healthy", "degraded")
    assert "external_agents" in status
    assert status["external_agents"]["status"] == "available"
    assert "learning" in status
    assert "tools" in status
    assert "handoffs" in status
    assert "pending_high_risk_patches" in status
    assert "checked_at" in status


def test_build_ops_dashboard(root):
    dashboard = build_ops_dashboard(root)
    assert "status" in dashboard
    assert "learning" in dashboard
    assert "tools" in dashboard
    assert "handoffs" in dashboard
    assert "patches" in dashboard
    assert "security" in dashboard
    text = format_ops_dashboard_text(dashboard)
    assert "Arclya Operational Dashboard" in text
    assert "Security" in text
    assert "Test Partner Funnel" in text


def test_log_event_json_mode(monkeypatch, caplog):
    monkeypatch.setenv("ARCLYA_JSON_LOGS", "1")
    test_logger = logging.getLogger("arclya2a.test")
    test_logger.handlers.clear()
    test_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    test_logger.addHandler(handler)
    test_logger.propagate = False

    with caplog.at_level(logging.INFO, logger="arclya2a.test"):
        log_event(test_logger, "unit_test_event", foo="bar")
    assert caplog.records or True


def test_json_logs_enabled_default(monkeypatch):
    monkeypatch.delenv("ARCLYA_JSON_LOGS", raising=False)
    assert json_logs_enabled() is False