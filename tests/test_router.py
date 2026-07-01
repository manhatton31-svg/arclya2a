from arclya2a.orchestrator.router import (
    is_onboarding_complete,
    is_warm_lead,
    route_entry_agent,
    resolve_flow_chain,
)


def test_route_new_agent_to_onboarding():
    ssot = {"deal_id": "d1", "summary": "New agent", "stage": "new", "metadata": {}}
    assert route_entry_agent(ssot) == "onboarding_specialist"


def test_route_onboarded_warm_lead_to_closer():
    ssot = {
        "deal_id": "d2",
        "summary": "Warm lead",
        "stage": "warm_lead",
        "metadata": {
            "onboarding_complete": True,
            "product_profile_complete": True,
            "product_profile": {
                "agent_name": "Acme Agent",
                "product_name": "SaaS Tool",
                "product_description": "AI outreach platform",
                "target_customer": "B2B SaaS founders",
                "typical_deal_size": "$99/mo",
                "common_objections": ["Too expensive"],
                "preferred_pricing_model": "subscription",
                "accepts_crypto": False,
                "destination_link": "https://example.com/signup",
            },
            "lead_warmth": "warm",
        },
    }
    assert is_onboarding_complete(ssot)
    assert is_warm_lead(ssot)
    assert route_entry_agent(ssot) == "closer"


def test_resolve_onboarding_flow_chain(root):
    import json
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        agents = {a["id"]: a for a in json.load(f)["agents"]}
    chain = resolve_flow_chain(agents, "onboarding_specialist")
    assert chain[0] == "onboarding_specialist"
    assert "profit_guardrail" in chain
    assert "final_arbiter" in chain


def test_resolve_closer_flow_chain(root):
    import json
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        agents = {a["id"]: a for a in json.load(f)["agents"]}
    chain = resolve_flow_chain(agents, "closer")
    assert chain[0] == "closer"
    assert chain[-1] == "final_arbiter"


def test_resolve_recruiter_flow_chain_skips_onboarding(root):
    import json
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        agents = {a["id"]: a for a in json.load(f)["agents"]}
    chain = resolve_flow_chain(agents, "recruiter")
    assert chain[0] == "recruiter"
    assert "onboarding_specialist" not in chain
    assert "profit_guardrail" in chain
    assert chain[-1] == "final_arbiter"