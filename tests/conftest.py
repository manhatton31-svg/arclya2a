import json as json_module
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
_AGENT_CONTEXT = {"id": "outreach_worker"}


def _mock_xai_response(agent_id: str) -> dict:
    responses = {
        "outreach_worker": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "draft": {
                "subject": "Partnership opportunity for TestCo",
                "body": "Hi — tailored outreach for deal deal_test.",
            },
            "validation": {"confidence": 78, "check": "Draft includes CTA and personalization"},
            "preference_handshake": {"format": "json", "accepted": True},
        },
        "profit_guardrail": {
            "status": "COMPLETE",
            "next_action": "handoff_to_final_arbiter",
            "validation": {"confidence": 92, "check": "Margin review complete"},
        },
        "final_arbiter": {
            "status": "COMPLETE",
            "next_action": "deliver_to_customer",
            "qc_result": {"passed": True, "issues": []},
            "validation": {"confidence": 94, "check": "QC gate review complete"},
        },
        "meta_optimizer": {
            "status": "COMPLETE",
            "next_action": "update_learning_store",
            "improvement_signal": {
                "deltas": {"open_rate": -0.07},
                "recommendations": ["Improve subject lines for open rate"],
                "priority": "high",
            },
            "validation": {"confidence": 85, "check": "Learning signal generated"},
        },
        "onboarding_specialist": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "product_profile": {
                "agent_name": "Test Agent",
                "product_name": "Test Product",
                "product_description": "Agent-to-agent outreach platform with pay-on-close lead routing.",
                "target_customer": "SaaS founders",
                "typical_deal_size": "$49/mo",
                "common_objections": ["Price too high", "Unclear ROI", "Integration effort"],
                "preferred_pricing_model": "subscription",
                "accepts_crypto": False,
                "destination_link": "https://example.com/signup",
                "affiliate_code": "TEST123",
            },
            "onboarding_complete": True,
            "validation": {"confidence": 88, "check": "Profile complete"},
        },
        "closer": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "close_package": {
                "subject": "Confirm lead routing partnership",
                "body": "Pay-on-close terms; route leads to tracked URL",
                "cta_url": "https://example.com/buy",
                "lead_routing_confirmed": True,
                "partner_agent_commitment": "Will send qualified leads to destination_link",
                "objections_handled": ["Budget"],
                "pricing_summary": "Success-based: pay only on closed leads via affiliate tracking",
                "pricing_model": "success_based_pay_on_close",
                "close_type": "lead_routing_commitment",
            },
            "validation": {"confidence": 90, "check": "Lead routing commitment secured"},
        },
        "recruiter": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "ready_to_send": True,
            "outreach_message": {
                "subject": "Prospect Agent × Test Product — warm lead routing (pay-on-close)",
                "body": "Your outreach capability aligns with sellers we onboard. Test Product serves SaaS founders. Success-based — pay only on convert. Reply with your Agent Card URL for a sandbox dry-run.",
                "cta_type": "sandbox_handoff",
                "personalized_value_proposition": "Route warm SaaS founder leads to Test Product on pay-on-close terms.",
            },
            "send_instructions": {
                "delivery": "a2a_handoff",
                "target_url": "https://prospect.example",
                "recommended_headers": ["X-Arclya-Key", "X-Arclya-Agent-Id"],
                "follow_up": "On positive reply, handoff_to_closer",
            },
            "recruitment_draft": {
                "target_agent_id": "prospect_agent",
                "subject": "Join Arclya A2A",
                "body": "Agent-to-agent partnership invite",
                "ready_to_send": True,
                "value_props": ["Low marginal cost"],
                "proposed_handoff_chain": ["closer"],
            },
            "acquisition_stage": "invited",
            "validation": {"confidence": 80, "check": "Recruitment draft ready"},
        },
    }
    return responses.get(
        agent_id,
        {"status": "COMPLETE", "next_action": "continue", "validation": {"confidence": 70, "check": "ok"}},
    )


@pytest.fixture(autouse=True)
def isolate_settings_from_dotenv(monkeypatch):
    """Prevent developer shell/.env from affecting test environment."""
    from arclya2a.settings import reset_dotenv_state

    reset_dotenv_state()
    monkeypatch.setenv("ARCLYA_SKIP_DOTENV", "1")
    monkeypatch.delenv("ARCLYA_API_KEY", raising=False)
    monkeypatch.setenv("ARCLYA_SANDBOX_FORCE_DRY_RUN", "1")


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def mock_xai(monkeypatch):
    """Mock xAI inference without patching global httpx (TestClient uses httpx too)."""
    from arclya2a.xai.client import XAIClient

    def mock_chat_completion(self, *, messages, model, agent_id):
        body = _mock_xai_response(agent_id)
        cost_record = self.record_cost(
            agent_id=agent_id,
            model=model,
            input_tokens=500,
            output_tokens=200,
            cached_input_tokens=400,
        )
        return {
            "choices": [{"message": {"content": json_module.dumps(body)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "cached_tokens": 400},
            "cost_record": cost_record,
        }

    monkeypatch.setattr(XAIClient, "chat_completion", mock_chat_completion)
    return XAIClient(ROOT, api_key="test-key-mock")