"""Tests for security observability stream, metrics, and API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.observability.security_events import (
    EVENT_INJECTION_SCAN_REJECTION,
    EVENT_TOOL_GATE_BLOCK,
    build_security_metrics,
    list_security_events,
    record_security_event,
)
from arclya2a.security.injection_scanner import InjectionScanResult, record_scan_event
from arclya2a.server.app import create_app
from arclya2a.tools.gating import ToolGateResult, log_gate_decision


def test_record_security_event_writes_stream_and_audit(root):
    event = record_security_event(
        root,
        EVENT_TOOL_GATE_BLOCK,
        reason_code="COMMITMENT_NOT_CONFIRMED",
        partner_id="tp_obs_test",
        agent_id="closer",
        severity="medium",
        details={"tool_id": "gmail.send_followup_email"},
    )
    assert event["id"]
    assert event["audit_id"]
    stream = root / "data" / "security" / "security_events.jsonl"
    assert stream.exists()
    assert "COMMITMENT_NOT_CONFIRMED" in stream.read_text(encoding="utf-8")


def test_list_security_events_filters(root):
    record_security_event(
        root, EVENT_TOOL_GATE_BLOCK, reason_code="A", partner_id="tp_a", severity="medium",
    )
    record_security_event(
        root, EVENT_INJECTION_SCAN_REJECTION, reason_code="disqualify", partner_id="tp_b", severity="high",
    )
    tool_events = list_security_events(root, event_type=EVENT_TOOL_GATE_BLOCK, hours=1, limit=10)
    partner_events = list_security_events(root, partner_id="tp_b", hours=1, limit=10)
    assert len(tool_events) >= 1
    assert all(e["event_type"] == EVENT_TOOL_GATE_BLOCK for e in tool_events)
    assert partner_events and partner_events[0]["partner_id"] == "tp_b"


def test_injection_scan_records_security_stream(root):
    result = InjectionScanResult(
        is_suspicious=True,
        confidence=0.9,
        detected_patterns=[{"id": "instruction_override", "severity": 0.9}],
        recommended_action="reject",
        agent_id="closer",
        scan_id="scan_obs_test",
    )
    record_scan_event(root, result, partner_id="tp_scan", deal_id="d1")
    events = list_security_events(root, event_type=EVENT_INJECTION_SCAN_REJECTION, hours=1)
    assert any(e.get("scan_id") == "scan_obs_test" for e in events)


def test_tool_gate_block_records_security_stream(root):
    result = ToolGateResult(
        allowed=False,
        reason="blocked",
        blocked_reason_code="PARTNER_REQUEST_NOT_GATE",
    )
    log_gate_decision(
        root,
        agent_id="closer",
        tool_id="gmail.send_followup_email",
        result=result,
        context={"partner_id": "tp_gate", "ssot": {"deal_id": "d2"}},
    )
    events = list_security_events(root, event_type=EVENT_TOOL_GATE_BLOCK, hours=1)
    assert any(e.get("reason_code") == "PARTNER_REQUEST_NOT_GATE" for e in events)


def test_build_security_metrics_includes_counts(root):
    record_security_event(root, EVENT_TOOL_GATE_BLOCK, reason_code="X", severity="medium")
    metrics = build_security_metrics(root)
    assert "counts_24h" in metrics
    assert "counts_7d" in metrics
    assert "recent_incidents" in metrics
    assert "trend_7d" in metrics
    assert metrics["counts_24h"]["total"] >= 1


def test_ops_dashboard_includes_security_section(root):
    record_security_event(root, EVENT_TOOL_GATE_BLOCK, reason_code="Y", severity="low")
    dashboard = build_ops_dashboard(root)
    assert "security" in dashboard
    assert dashboard["security"]["counts_24h"]["total"] >= 1
    assert "security_outcomes" in dashboard["patches"]


def test_security_events_api(root):
    record_security_event(
        root, EVENT_INJECTION_SCAN_REJECTION, reason_code="reject", partner_id="tp_api",
    )
    client = TestClient(create_app(root))
    resp = client.get("/security/events", params={"partner_id": "tp_api", "hours": 24})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "metrics" in data
    assert any(e.get("partner_id") == "tp_api" for e in data["events"])