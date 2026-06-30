import json as json_module
from pathlib import Path
from unittest.mock import MagicMock

import httpx
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
    }
    return responses.get(
        agent_id,
        {"status": "COMPLETE", "next_action": "continue", "validation": {"confidence": 70, "check": "ok"}},
    )


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def mock_xai(monkeypatch):
    """Mock httpx at network boundary; exercises real XAIClient.chat_completion."""
    from arclya2a.xai.client import XAIClient

    original_chat = XAIClient.chat_completion

    def wrapped_chat(self, *, messages, model, agent_id):
        _AGENT_CONTEXT["id"] = agent_id
        return original_chat(self, messages=messages, model=model, agent_id=agent_id)

    def fake_post(self, url, json=None, headers=None, **kwargs):
        body = _mock_xai_response(_AGENT_CONTEXT["id"])
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": json_module.dumps(body)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "cached_tokens": 400},
        }
        return response

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(XAIClient, "chat_completion", wrapped_chat)
    return XAIClient(ROOT, api_key="test-key-mock")