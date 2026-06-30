import json

from arclya2a.handoff.validators import validate_role_card


def test_registry_role_cards(root):
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        registry = json.load(f)
    for agent in registry["agents"]:
        validate_role_card(agent["role_card"])