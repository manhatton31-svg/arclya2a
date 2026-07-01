from arclya2a.orchestrator.agent_runner import _validate_onboarding_specialist


def test_onboarding_rejects_incomplete_profile_claim(root):
    agent = {"id": "onboarding_specialist", "handoff_targets": ["profit_guardrail"]}
    handoff = {
        "status": "COMPLETE",
        "next_action": "handoff_to_profit_guardrail",
        "payload": {
            "onboarding_complete": True,
            "product_profile": {
                "agent_name": "X",
                "product_name": "Y",
                "product_description": "short",
                "target_customer": "",
                "typical_deal_size": "",
                "common_objections": [],
                "preferred_pricing_model": "subscription",
                "accepts_crypto": False,
                "destination_link": "bad-url",
            },
        },
        "validation": {"confidence": 90, "check": "claimed complete"},
    }
    result = _validate_onboarding_specialist(agent, handoff, root, {"ssot": {}})
    assert result["payload"]["onboarding_complete"] is False
    assert result["next_action"] == "continue_onboarding"
    assert result["ssot_updates"]["metadata"]["product_profile_complete"] is False


def test_onboarding_saves_complete_profile(root, tmp_path):
    agent = {"id": "onboarding_specialist", "handoff_targets": ["profit_guardrail"]}
    profile = {
        "agent_name": "Save Test",
        "product_name": "Product Z",
        "product_description": "A complete agent-to-agent lead routing product for SaaS.",
        "target_customer": "Agent operators",
        "typical_deal_size": "$40/close",
        "common_objections": ["A", "B", "C"],
        "preferred_pricing_model": "success_based",
        "accepts_crypto": True,
        "destination_link": "https://example.com/go",
        "affiliate_code": "SAVE1",
    }
    handoff = {
        "status": "COMPLETE",
        "payload": {"onboarding_complete": True, "product_profile": profile},
        "validation": {"confidence": 92, "check": "All fields validated"},
    }
    result = _validate_onboarding_specialist(agent, handoff, root, {"ssot": {}})
    assert result["payload"]["onboarding_complete"] is True
    assert result["ssot_updates"]["metadata"]["product_profile_complete"] is True
    saved = root / "config" / "profiles" / "save_test.json"
    assert saved.exists()
    assert "ref=SAVE1" in result["ssot_updates"]["metadata"]["destination_cta"] or "SAVE1" in str(saved.read_text())