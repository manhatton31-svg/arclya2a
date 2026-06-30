from pathlib import Path

from arclya2a.audit.logger import read_audit_records
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.orchestrator.agent_runner import resolve_chain_from_registry


def test_resolve_chain_from_registry(root):
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        import json
        agents = {a["id"]: a for a in json.load(f)["agents"]}
    chain = resolve_chain_from_registry(agents, "outreach_worker")
    assert chain == ["outreach_worker", "profit_guardrail", "final_arbiter"]


def test_multi_agent_handoff_chain_uses_xai(root, mock_xai):
    orchestrator = Orchestrator(root, xai_client=mock_xai)
    initial_ssot = {
        "deal_id": "deal_test_001",
        "summary": "Test deal",
        "customer": {"company": "TestCo"},
        "deal_value_usd": 49.0,
        "stage": "new",
        "metadata": {},
    }
    result = orchestrator.run_chain(
        initial_ssot=initial_ssot,
        task_context="Write outreach email",
        revenue_usd=49.0,
        estimated_cost_usd=5.0,
    )

    assert not result.emergency_stop
    assert len(result.handoff_chain) == 3

    for handoff in result.handoff_chain:
        assert handoff["status"] == "COMPLETE"
        assert handoff.get("next_action")
        assert handoff.get("memory_summary")
        assert 0 <= handoff["validation"]["confidence"] <= 100
        assert handoff.get("inference", {}).get("prompt_assembled") is True

    terminal = result.handoff_chain[-1]
    assert terminal["next_action"] == "deliver_to_customer"
    assert result.final_ssot["stage"] == "qc_passed"
    assert len(result.cost_records) == 3

    feedback = result.handoff_chain[1].get("feedback")
    assert feedback["from_agent"] == "profit_guardrail"
    assert feedback["to_agent"] == "outreach_worker"

    audits = read_audit_records(root, limit=10)
    assert len(audits) >= 3


def test_emergency_stop_records_cost(root, mock_xai):
    orchestrator = Orchestrator(root, xai_client=mock_xai)
    result = orchestrator.run_chain(
        chain=["outreach_worker", "profit_guardrail"],
        initial_ssot={"deal_id": "d2", "summary": "Low margin", "stage": "new", "metadata": {}},
        task_context="test",
        revenue_usd=10.0,
        estimated_cost_usd=9.8,
    )
    assert result.emergency_stop
    assert result.handoff_chain[-1]["status"] == "EMERGENCY_STOP"
    assert len(result.cost_records) == 2


def test_meta_optimizer_chain(root, mock_xai):
    orchestrator = Orchestrator(root, xai_client=mock_xai)
    result = orchestrator.run_chain(
        chain=["meta_optimizer"],
        initial_ssot={"deal_id": "d3", "summary": "Learning", "stage": "closed", "metadata": {}},
        task_context="Analyze campaign",
    )
    assert len(result.handoff_chain) == 1
    handoff = result.handoff_chain[0]
    assert handoff["agent_id"] == "meta_optimizer"
    assert handoff["payload"].get("prompt_patch")
    assert (root / "prompts" / "outreach_worker_learned.md").exists()