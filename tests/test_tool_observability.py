"""Tests for tool retry, observability, and structured errors."""

from __future__ import annotations

import json as json_module
from unittest.mock import MagicMock

import httpx
import pytest

from arclya2a.audit.logger import read_audit_records
from arclya2a.connectors.base import ConnectorResult
from arclya2a.connectors.gmail import GmailConnector
from arclya2a.tools.errors import RATE_LIMITED, TRANSIENT_HTTP, UNKNOWN_TOOL
from arclya2a.tools.executor import execute_tool_requests
from arclya2a.tools.observability import execution_summary, list_tool_executions


def _gate_context(**extra):
    base = {
        "agent_output": {
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "validation": {"confidence": 90},
        }
    }
    base.update(extra)
    return base


def test_structured_error_on_unknown_tool(root):
    results = execute_tool_requests(
        root,
        "closer",
        [{"tool_id": "fake.tool", "parameters": {}, "reason": "test"}],
        context=_gate_context(handoff_id="h1"),
    )
    assert len(results) == 1
    assert results[0]["error_code"] == UNKNOWN_TOOL
    assert results[0]["outcome"] == "skipped"
    assert results[0]["audit_id"]


def test_tool_execution_recorded_and_audited(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    results = execute_tool_requests(
        root,
        "closer",
        [{
            "tool_id": "linear.create_followup_task",
            "reason": "Deal closed",
            "parameters": {"title": "Follow up: Observability Test"},
        }],
        context=_gate_context(handoff_id="audit_handoff_1"),
    )
    assert results[0]["success"]
    assert results[0]["duration_ms"] >= 0
    assert results[0]["attempts"] == 1

    logs = list_tool_executions(root, limit=5, tool_id="linear.create_followup_task")
    assert any(r.get("reason") == "Deal closed" for r in logs)

    audits = read_audit_records(root, limit=20)
    tool_audits = [a for a in audits if a.get("action", "").startswith("tool_execute")]
    assert tool_audits


def test_retry_on_transient_failure(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_MAX_RETRIES", "3")
    monkeypatch.setenv("ARCLYA_TOOL_RETRY_BASE_MS", "10")
    monkeypatch.delenv("ARCLYA_TOOL_DRY_RUN", raising=False)
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "test-token")

    calls = {"n": 0}

    def flaky_execute(self, *, tool_id, action, params, tool_def):
        calls["n"] += 1
        if calls["n"] < 3:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector="gmail",
                action=action,
                error="HTTP 503: Service Unavailable",
                error_code=TRANSIENT_HTTP,
                transient=True,
            )
        return ConnectorResult(
            success=True,
            tool_id=tool_id,
            connector="gmail",
            action=action,
            data={"message_id": "msg_ok", "to": params["to"], "subject": params["subject"]},
        )

    monkeypatch.setattr(GmailConnector, "execute", flaky_execute)

    results = execute_tool_requests(
        root,
        "closer",
        [{
            "tool_id": "gmail.send_followup_email",
            "reason": "Retry test",
            "parameters": {
                "to": "partner@example.com",
                "subject": "Hi",
                "body": "Test body",
            },
        }],
        context=_gate_context(),
    )
    assert results[0]["success"]
    assert results[0]["attempts"] == 3
    assert calls["n"] == 3


def test_execution_summary(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    execute_tool_requests(
        root,
        "closer",
        [{"tool_id": "linear.create_followup_task", "parameters": {"title": "Sum test"}, "reason": "r"}],
        context=_gate_context(),
    )
    summary = execution_summary(root, limit=10)
    assert summary["total"] >= 1


def test_tools_executions_endpoint(root, mock_xai):
    from fastapi.testclient import TestClient
    from arclya2a.server.app import create_app

    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/tools/executions", params={"limit": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert "executions" in data
    assert "summary" in data


def test_classify_rate_limit_transient():
    from arclya2a.connectors.http_helpers import classify_http_status

    code, transient = classify_http_status(429)
    assert code == RATE_LIMITED
    assert transient is True