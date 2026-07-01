"""Tests for Tool Registry and connector execution."""

from __future__ import annotations

import json as json_module
import os

import pytest

from arclya2a.connectors.gmail import GmailConnector
from arclya2a.connectors.linear import LinearConnector
from arclya2a.orchestrator.agent_runner import run_registry_agent
from arclya2a.tools.executor import execute_tool_requests
from arclya2a.tools.registry import ToolRegistry


def test_tool_registry_loads_definitions(root):
    registry = ToolRegistry(root)
    assert len(registry.tools) >= 5
    assert registry.get_tool("linear.create_followup_task") is not None


def test_tool_catalog_for_closer(root):
    registry = ToolRegistry(root)
    catalog = registry.catalog_for_agent("closer")
    tool_ids = {t["id"] for t in catalog}
    assert "linear.create_followup_task" in tool_ids
    assert "gmail.send_followup_email" in tool_ids
    assert "notion.create_deal_page" in tool_ids


def test_tool_catalog_excludes_unauthorized_agent(root):
    registry = ToolRegistry(root)
    catalog = registry.catalog_for_agent("profit_guardrail")
    assert catalog == []


def _gate_context():
    return {
        "agent_output": {
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "validation": {"confidence": 90},
        }
    }


def test_execute_tool_requests_dry_run(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    results = execute_tool_requests(
        root,
        "closer",
        [
            {
                "tool_id": "linear.create_followup_task",
                "reason": "Deal closed",
                "parameters": {
                    "title": "Follow up: Test Deal",
                    "description": "Partner committed",
                },
            },
            {
                "tool_id": "gmail.send_followup_email",
                "reason": "Confirmation email",
                "parameters": {
                    "to": "partner@example.com",
                    "subject": "Routing confirmed",
                    "body": "Thank you for the commitment.",
                },
            },
        ],
        context=_gate_context(),
    )
    assert len(results) == 2
    assert all(r["success"] for r in results)
    assert all(r.get("dry_run") for r in results)


def test_execute_rejects_unknown_tool(root):
    results = execute_tool_requests(
        root,
        "closer",
        [{"tool_id": "nonexistent.tool", "parameters": {}}],
        context=_gate_context(),
    )
    assert len(results) == 1
    assert not results[0]["success"]
    assert "Unknown tool" in results[0]["error"]


def test_execute_rejects_agent_not_allowed(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    results = execute_tool_requests(
        root,
        "profit_guardrail",
        [{"tool_id": "linear.create_followup_task", "parameters": {"title": "x"}}],
        context=_gate_context(),
    )
    assert not results[0]["success"]
    assert "not allowed" in results[0]["error"]


def test_gmail_connector_dry_run(monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    result = GmailConnector().execute(
        tool_id="gmail.send_followup_email",
        action="send_email",
        params={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
        },
        tool_def={},
    )
    assert result.success
    assert result.dry_run
    assert result.data["to"] == "a@b.com"


def test_linear_connector_dry_run(monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")
    result = LinearConnector().execute(
        tool_id="linear.create_followup_task",
        action="create_issue",
        params={"title": "Follow up deal"},
        tool_def={},
    )
    assert result.success
    assert result.data["identifier"] == "ARC-DRY"


def test_closer_agent_executes_tool_requests(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_TOOL_DRY_RUN", "1")

    import httpx
    from unittest.mock import MagicMock

    from arclya2a.xai.client import XAIClient

    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        agent = next(a for a in json_module.load(f)["agents"] if a["id"] == "closer")

    ssot = {
        "deal_id": "tool_test_001",
        "summary": "Warm partner close",
        "stage": "warm_lead",
        "metadata": {
            "onboarding_complete": True,
            "product_profile_complete": True,
            "product_profile": {
                "agent_name": "Test Agent",
                "product_name": "Test Product",
                "product_description": "A2A platform",
                "target_customer": "SaaS founders",
                "typical_deal_size": "$49",
                "common_objections": ["Price"],
                "preferred_pricing_model": "success_based",
                "accepts_crypto": False,
                "destination_link": "https://example.com/signup",
                "affiliate_code": "ref=test",
            },
        },
    }

    closer_body = {
        "status": "COMPLETE",
        "deal_closed": True,
        "lead_routing_confirmed": True,
        "close_type": "lead_routing_commitment",
        "close_package": {"cta_url": "https://example.com/signup?ref=test"},
        "validation": {"confidence": 90, "check": "closed"},
        "tool_requests": [
            {
                "tool_id": "linear.create_followup_task",
                "reason": "Deal closed",
                "parameters": {"title": "Follow up: Test Product"},
            }
        ],
    }

    def fake_post(self, url, json=None, headers=None, **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": json_module.dumps(closer_body)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "cached_tokens": 400},
        }
        return response

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    client = XAIClient(root, api_key="test-key-mock")

    handoff = run_registry_agent(
        agent,
        ssot,
        root,
        {"task_context": "Close deal", "revenue_usd": 50, "estimated_cost_usd": 5},
        xai_client=client,
    )

    tool_results = handoff["payload"].get("tool_results", [])
    assert len(tool_results) == 1
    assert tool_results[0]["success"]
    assert handoff["payload"].get("tools_executed") == 1


def test_registry_summary(root):
    summary = ToolRegistry(root).summary()
    assert summary["total_tools"] >= 5
    assert any(c["connector"] == "linear" for c in summary["connectors"])