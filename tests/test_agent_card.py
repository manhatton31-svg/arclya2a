import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient

from arclya2a.server.app import create_app, build_agent_card

ROOT = Path(__file__).resolve().parents[1]


def test_agent_card_schema():
    card = build_agent_card()
    schema_path = ROOT / "src" / "arclya2a" / "schemas" / "agent_card.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validate(card, schema)
    assert card["skills"]
    assert card.get("platform", {}).get("pricing_model") == "success_based"
    assert card.get("documentation")
    assert card.get("endpoints", {}).get("handoff_chain")
    assert "crypto_payments" in card.get("platform", {}).get("features", [])
    assert card.get("endpoints", {}).get("crypto_intent")
    assert card.get("endpoints", {}).get("crypto_submit")
    doc_rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "crypto-intent" in doc_rels
    assert "crypto-sales-guide" in doc_rels
    assert "first-crypto-sale-runbook" in doc_rels


def test_agent_card_endpoint():
    client = TestClient(create_app(ROOT))
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"]
    assert data["url"]
    assert data["capabilities"]
    assert data["defaultInputModes"]
    assert data["defaultOutputModes"]
    assert len(data["skills"]) > 0


def test_prompt_assembly_endpoint_includes_learned_context(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/prompt/assembly/outreach_worker")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_cacheable_section"]
    assert data["has_dynamic_section"]
    assert "{{learned_context}}" not in data["dynamic_context"]