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