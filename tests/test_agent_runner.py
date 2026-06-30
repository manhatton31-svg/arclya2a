import json

import pytest

from arclya2a.orchestrator.agent_runner import parse_agent_json_response, run_registry_agent
from arclya2a.orchestrator.error_policy import AgentExecutionError, execute_with_error_policy


def test_parse_agent_json_response():
    raw = 'Here is output:\n```json\n{"status": "COMPLETE", "next_action": "go"}\n```'
    parsed = parse_agent_json_response(raw)
    assert parsed["status"] == "COMPLETE"


def test_error_policy_retry_once_then_escalate(root):
    calls = {"n": 0}

    def fail_twice():
        calls["n"] += 1
        raise ValueError("fail")

    with pytest.raises(AgentExecutionError) as exc:
        execute_with_error_policy(
            "retry_once_then_escalate",
            fail_twice,
            root=root,
            agent_id="outreach_worker",
        )
    assert calls["n"] == 2
    assert exc.value.escalated


def test_run_registry_agent_uses_prompt_and_xai(root, mock_xai):
    import json as jsonlib
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        agent = next(a for a in jsonlib.load(f)["agents"] if a["id"] == "outreach_worker")

    ssot = {
        "deal_id": "d1",
        "summary": "Deal",
        "customer": {"company": "Acme"},
        "stage": "new",
        "metadata": {},
    }
    handoff = run_registry_agent(
        agent,
        ssot,
        root,
        {"task_context": "Draft email", "revenue_usd": 49, "estimated_cost_usd": 5},
        xai_client=mock_xai,
    )
    assert handoff["inference"]["prompt_assembled"] is True
    assert handoff["payload"]["draft"]["subject"]