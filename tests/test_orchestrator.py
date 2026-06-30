from arclya2a.audit.logger import read_audit_records
from arclya2a.orchestrator.engine import Orchestrator


def test_multi_agent_handoff_chain(root):
    orchestrator = Orchestrator(root)
    initial_ssot = {
        "deal_id": "deal_test_001",
        "summary": "Test deal",
        "customer": {"company": "TestCo"},
        "deal_value_usd": 49.0,
        "stage": "new",
        "metadata": {},
    }
    result = orchestrator.run_chain(
        chain=["outreach_worker", "profit_guardrail", "final_arbiter"],
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

    terminal = result.handoff_chain[-1]
    assert terminal["status"] == "COMPLETE"
    assert terminal["next_action"] == "deliver_to_customer"
    assert result.final_ssot["stage"] == "qc_passed"
    assert result.audit_ids
    assert result.cost_records

    audits = read_audit_records(root, limit=10)
    assert len(audits) >= 3


def test_emergency_stop_on_margin_violation(root):
    orchestrator = Orchestrator(root)
    result = orchestrator.run_chain(
        chain=["outreach_worker", "profit_guardrail"],
        initial_ssot={"deal_id": "d2", "summary": "Low margin", "stage": "new"},
        task_context="test",
        revenue_usd=10.0,
        estimated_cost_usd=9.8,
    )
    assert result.emergency_stop
    assert result.handoff_chain[-1]["status"] == "EMERGENCY_STOP"