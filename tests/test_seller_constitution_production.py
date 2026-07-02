"""Production-mode seller constitution: full A2A lifecycle without rehearsal fast-path."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.orchestrator.router import resolve_flow_chain, route_entry_agent
from arclya2a.server.app import create_app

CONSTITUTIONAL_TAIL = ("profit_guardrail", "final_arbiter")

WARM_PROFILE = {
    "agent_name": "Production Seller",
    "product_name": "Arclya Lead Router",
    "product_description": "Agent-to-agent platform routing warm qualified leads with pay-on-close tracking.",
    "target_customer": "B2B SaaS agent operators",
    "typical_deal_size": "$50 per closed lead",
    "common_objections": ["Unclear conversion tracking", "Partner quality concerns"],
    "preferred_pricing_model": "success_based",
    "accepts_crypto": False,
    "destination_link": "https://prod.example/signup",
    "affiliate_code": "prod_seller_01",
}


def _agents_run(handoff_chain: list[dict]) -> list[str]:
    return [h.get("agent_id") for h in handoff_chain if h.get("agent_id")]


def _qc_passed(handoff_chain: list[dict]) -> bool | None:
    for handoff in handoff_chain:
        if handoff.get("agent_id") == "final_arbiter":
            return (handoff.get("payload") or {}).get("qc_result", {}).get("passed")
    return None


def _margin_approved(handoff_chain: list[dict]) -> bool | None:
    for handoff in handoff_chain:
        if handoff.get("agent_id") == "profit_guardrail":
            return (handoff.get("payload") or {}).get("margin_check", {}).get("approved")
    return None


def _assert_production_constitutional_phase(
    *,
    phase_name: str,
    result,
    orchestrator: Orchestrator,
    expected_entry: str,
) -> dict:
    """Assert full constitutional chain in production mode (no sandbox fast-path)."""
    expected_chain = resolve_flow_chain(orchestrator.agents, expected_entry)
    agents_run = _agents_run(result.handoff_chain)

    assert result.entry_agent == expected_entry, phase_name
    assert agents_run == expected_chain, f"{phase_name}: expected {expected_chain}, got {agents_run}"
    assert not result.emergency_stop, phase_name
    assert _margin_approved(result.handoff_chain) is True, phase_name
    assert _qc_passed(result.handoff_chain) is True, phase_name
    assert result.final_ssot.get("stage") == "qc_passed", phase_name

    if phase_name == "recruiter":
        assert "onboarding_specialist" not in agents_run

    return {
        "phase": phase_name,
        "entry_agent": expected_entry,
        "agents_run": agents_run,
        "margin_approved": True,
        "qc_passed": True,
        "stage": result.final_ssot.get("stage"),
    }


@pytest.fixture
def production_orchestrator(root, mock_xai, monkeypatch):
    """Production seller context: no sandbox, no rehearsal fast-path."""
    monkeypatch.setenv("ARCLYA_REHEARSAL_MODE", "0")
    return Orchestrator(root, xai_client=mock_xai)


def test_production_seller_lifecycle_full_constitutional_chain(production_orchestrator):
    """Onboarding → Recruiter → Closer, each with entry → profit_guardrail → final_arbiter."""
    orch = production_orchestrator
    phases: list[dict] = []

    onboarding_ssot = {
        "deal_id": "prod_constitution_onboard",
        "summary": "New production seller",
        "stage": "new",
        "metadata": {},
    }
    assert route_entry_agent(onboarding_ssot) == "onboarding_specialist"
    onboarding = orch.run_chain(
        initial_ssot=onboarding_ssot,
        task_context="Production seller onboarding — complete product profile",
        auto_route=True,
        sandbox_mode=False,
    )
    phases.append(
        _assert_production_constitutional_phase(
            phase_name="onboarding",
            result=onboarding,
            orchestrator=orch,
            expected_entry="onboarding_specialist",
        )
    )
    meta = onboarding.final_ssot.get("metadata", {})
    assert meta.get("onboarding_complete") is True
    assert meta.get("product_profile_complete") is True
    assert meta.get("destination_cta")

    recruit_ssot = dict(onboarding.final_ssot)
    recruit_ssot["stage"] = "recruiting"
    recruit_ssot.setdefault("metadata", {})["acquisition_stage"] = "prospect"
    assert route_entry_agent(recruit_ssot) == "recruiter"
    recruit = orch.run_chain(
        initial_ssot=recruit_ssot,
        task_context="Production partner acquisition for onboarded seller",
        auto_route=True,
        sandbox_mode=False,
    )
    phases.append(
        _assert_production_constitutional_phase(
            phase_name="recruiter",
            result=recruit,
            orchestrator=orch,
            expected_entry="recruiter",
        )
    )
    assert recruit.final_ssot.get("metadata", {}).get("acquisition_stage")

    close_ssot = dict(recruit.final_ssot)
    close_ssot["stage"] = "warm_lead"
    close_ssot.setdefault("metadata", {})["lead_warmth"] = "warm"
    close_ssot["metadata"]["product_profile"] = meta.get("product_profile", WARM_PROFILE)
    assert route_entry_agent(close_ssot) == "closer"
    close = orch.run_chain(
        initial_ssot=close_ssot,
        task_context="Production close — secure lead routing commitment",
        auto_route=True,
        sandbox_mode=False,
        revenue_usd=49.0,
        estimated_cost_usd=5.0,
    )
    phases.append(
        _assert_production_constitutional_phase(
            phase_name="closer",
            result=close,
            orchestrator=orch,
            expected_entry="closer",
        )
    )

    closer_payload = next(
        h.get("payload", {}) for h in close.handoff_chain if h.get("agent_id") == "closer"
    )
    assert closer_payload.get("deal_closed") is True
    assert closer_payload.get("lead_routing_confirmed") is True
    assert closer_payload.get("close_type") == "lead_routing_commitment"
    assert (closer_payload.get("close_package") or {}).get("cta_url")

    assert all(p["qc_passed"] for p in phases)
    assert all(p["margin_approved"] for p in phases)
    assert all(CONSTITUTIONAL_TAIL[-1] in p["agents_run"] for p in phases)


def test_production_handoff_chain_http_end_to_end(root, mock_xai, monkeypatch):
    """HTTP path runs full guardrail chain for production callers (non-sandbox API key)."""
    monkeypatch.setenv("ARCLYA_REHEARSAL_MODE", "0")
    monkeypatch.setenv("ARCLYA_API_KEY", "prod-constitution-key")
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="prod-constitution-key"))

    ssot_after_onboarding: dict = {"metadata": {}}
    for phase, payload in [
        (
            "onboarding",
            {
                "deal_id": "http_prod_onboard",
                "task_context": "HTTP production onboarding",
                "auto_route": True,
                "revenue_usd": 49.0,
                "estimated_cost_usd": 5.0,
            },
        ),
        (
            "recruiter",
            {
                "deal_id": "http_prod_recruit",
                "task_context": "HTTP production recruitment",
                "auto_route": True,
                "onboarding_complete": True,
                "acquisition_stage": "prospect",
                "revenue_usd": 49.0,
                "estimated_cost_usd": 5.0,
            },
        ),
        (
            "closer",
            {
                "deal_id": "http_prod_close",
                "task_context": "HTTP production close",
                "auto_route": True,
                "onboarding_complete": True,
                "lead_warmth": "warm",
                "product_profile": WARM_PROFILE,
                "revenue_usd": 49.0,
                "estimated_cost_usd": 5.0,
            },
        ),
    ]:
        resp = client.post(
            "/orchestrate/handoff-chain",
            json=payload,
            headers={"X-Arclya-Key": "prod-constitution-key"},
        )
        assert resp.status_code == 200, (phase, resp.text)
        data = resp.json()
        summary = data["summary"]
        agents = summary["agents_executed"]
        assert agents[0] == {
            "onboarding": "onboarding_specialist",
            "recruiter": "recruiter",
            "closer": "closer",
        }[phase]
        assert agents[-2:] == list(CONSTITUTIONAL_TAIL), phase
        assert summary.get("margin_approved") is True, phase
        assert summary.get("qc_passed") is True, phase
        assert data.get("emergency_stop") is False, phase

        if phase == "onboarding":
            assert summary.get("onboarding_complete") is True
            ssot_after_onboarding = data["final_ssot"]
        if phase == "closer":
            assert summary.get("deal_closed") is True
            assert summary.get("lead_routing_confirmed") is True
            assert summary.get("close_type") == "lead_routing_commitment"

    assert ssot_after_onboarding.get("metadata", {}).get("product_profile_complete") is True


def test_sandbox_fast_chain_disabled_in_production_settings(root, mock_xai, monkeypatch):
    """ARCLYA_REHEARSAL_MODE=0 keeps sandbox on single-agent chains only when explicitly sandbox."""
    from arclya2a.orchestrator.engine import _use_sandbox_fast_chain
    from arclya2a.settings import reset_dotenv_state

    monkeypatch.setenv("ARCLYA_REHEARSAL_MODE", "0")
    reset_dotenv_state()
    assert _use_sandbox_fast_chain(True) is False
    assert _use_sandbox_fast_chain(False) is False

    orch = Orchestrator(root, xai_client=mock_xai)
    result = orch.run_chain(
        initial_ssot={
            "deal_id": "sandbox_still_fast_off",
            "summary": "Sandbox",
            "stage": "recruiting",
            "metadata": {"onboarding_complete": True, "acquisition_stage": "prospect"},
        },
        task_context="sandbox recruit",
        sandbox_mode=True,
    )
    agents = _agents_run(result.handoff_chain)
    assert "profit_guardrail" in agents
    assert "final_arbiter" in agents