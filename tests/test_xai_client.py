import os

import pytest

from arclya2a.xai.client import XAIClient, assemble_prompt, select_model


def test_select_cheapest_model(root):
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        import json
        core = json.load(f)
    assert select_model("economy", core) == "grok-3-mini"


def test_prompt_assembly_separates_cacheable_dynamic(root):
    prompt_path = root / "prompts" / "outreach_worker.md"
    assembly = assemble_prompt(
        prompt_path,
        agent_id="outreach_worker",
        model="grok-3-mini",
        variables={"ssot_snapshot": "{}", "memory_summary": "m", "task_context": "t"},
    )
    assert assembly.cacheable_instructions
    assert assembly.dynamic_context
    assert "System Instructions" in assembly.cacheable_instructions
    assert "Current Context" in assembly.dynamic_context
    assert assembly.cacheable_instructions not in assembly.dynamic_context


def test_record_cost(root):
    client = XAIClient(root)
    record = client.record_cost(
        agent_id="outreach_worker",
        model="grok-3-mini",
        input_tokens=1000,
        output_tokens=500,
        cached_input_tokens=800,
    )
    assert record["cost_usd"] > 0
    assert record["agent_id"] == "outreach_worker"


def test_chat_completion_returns_cost_record(root, mock_xai):
    client = mock_xai
    data = client.chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        model="grok-3-mini",
        agent_id="outreach_worker",
    )
    assert "cost_record" in data
    assert data["cost_record"]["cost_usd"] >= 0


def test_xai_call_requires_api_key(root):
    client = XAIClient(root, api_key=None)
    old = os.environ.pop("XAI_API_KEY", None)
    try:
        with pytest.raises(EnvironmentError, match="XAI_API_KEY"):
            client.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="grok-3-mini",
                agent_id="test",
            )
    finally:
        if old:
            os.environ["XAI_API_KEY"] = old