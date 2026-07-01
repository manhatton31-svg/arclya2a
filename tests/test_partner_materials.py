"""Tests for partner-facing outreach materials and recruiter prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arclya2a.server.app import build_agent_card, create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "doc_name",
    [
        "partner-outreach-value-proposition.md",
        "partnership-model-one-pager.md",
        "test-partner-onboarding-checklist.md",
    ],
)
def test_partner_docs_exist(doc_name):
    path = ROOT / "docs" / doc_name
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert len(text) > 200


def test_closer_prompt_injection_protection():
    text = (ROOT / "prompts" / "closer_prompt.md").read_text(encoding="utf-8")
    assert "{{security_block_full}}" in text
    assert "{{injection_scan_result}}" in text
    assert "tool_reasoning" in text
    assert "lead_routing_commitment" in text
    assert "partner_trust" in text
    assert "disqualification_reason" in text
    assert "Tool Execution Gating" in text
    from arclya2a.security.security_block import get_security_block

    assert "NEVER" in get_security_block("closer")


def test_onboarding_prompt_uses_security_module():
    text = (ROOT / "prompts" / "onboarding_prompt.md").read_text(encoding="utf-8")
    assert "{{security_block_compact}}" in text
    assert "{{injection_scan_result}}" in text


def test_recruiter_prompt_agent_card_personalization():
    text = (ROOT / "prompts" / "recruiter_prompt.md").read_text(encoding="utf-8")
    assert "partner_agent_card_summary" in text
    assert "personalization_hooks" in text
    assert "/.well-known/agent-card.json" in text
    assert "warm lead" in text.lower()
    assert "ready_to_send" in text
    assert "outreach_message" in text
    assert "send_instructions" in text


def test_agent_card_includes_test_partner_docs():
    card = build_agent_card()
    rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "test-partner-checklist" in rels
    assert "partnership-model" in rels
    assert "partner-outreach" in rels
    assert "sandbox-register" in rels
    assert "onboarding-guide" in rels
    assert "partner-progress" in rels
    endpoints = card.get("endpoints", {})
    assert "sandbox_register" in endpoints
    assert "partner_progress" in endpoints


def test_landing_page_test_partner_cta(root):
    client = TestClient(create_app(root))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "test partner" in resp.text.lower()
    assert "onboarding/validate" in resp.text
    assert "partners/sandbox/register" in resp.text
    assert "partners/onboarding/guide" in resp.text