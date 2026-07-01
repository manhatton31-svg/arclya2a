#!/usr/bin/env python3
"""End-to-end demo: new seller → onboarding → recruiter → closer.

Modes
-----
mock (default):
    Deterministic responses; no network calls. Safe for CI and local smoke tests.

live (--live):
    Real xAI inference via chat completions API.
    Requires XAI_API_KEY in the environment (or .env if loaded by your shell).

Each phase runs the full constitutional chain for its entry agent:
    entry_agent → profit_guardrail → final_arbiter

Usage
-----
    python scripts/demo_a2a_flow.py
    python scripts/demo_a2a_flow.py --json
    python scripts/demo_a2a_flow.py --live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.config.product_profile import build_destination_cta
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.orchestrator.router import resolve_flow_chain, route_entry_agent
from arclya2a.xai.client import XAIClient

# Constitutional tail appended by resolve_flow_chain for flow entry agents.
GUARDRAIL_CHAIN = ("profit_guardrail", "final_arbiter")

DEMO_PROFILE = {
    "agent_name": "Demo Seller Agent",
    "product_name": "Arclya Lead Router",
    "product_description": "Agent-to-agent platform that routes warm qualified leads with pay-on-close tracking.",
    "target_customer": "B2B SaaS agent operators",
    "typical_deal_size": "$50 per closed lead",
    "common_objections": [
        "Unclear conversion tracking",
        "Partner quality concerns",
        "Pay-on-close skepticism",
    ],
    "preferred_pricing_model": "success_based",
    "accepts_crypto": False,
    "destination_link": "https://demo.arclya.example/signup",
    "affiliate_code": "demo_partner_01",
}


def _mock_responses(agent_id: str) -> dict[str, Any]:
    """Deterministic LLM payloads for each agent in the demo flow."""
    responses: dict[str, dict[str, Any]] = {
        "onboarding_specialist": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "product_profile": DEMO_PROFILE,
            "onboarding_complete": True,
            "missing_fields": [],
            "validation_errors": [],
            "validation": {"confidence": 92, "check": "All profile fields collected and confirmed"},
            "preference_handshake": {"format": "json", "accepted": True},
        },
        "recruiter": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "recruitment_draft": {
                "target_agent_id": "partner_outreach_bot",
                "subject": "Warm lead partnership — Arclya Lead Router",
                "body": (
                    "Audience fit: B2B SaaS agent operators. Success-based pay-on-close — "
                    "route warm leads to tracked destination. Agent card verified."
                ),
                "value_props": [
                    "Warm qualified leads only",
                    "Success-based / pay-on-close",
                    "Tracked lead routing commitment via Closer",
                ],
                "proposed_handoff_chain": ["closer"],
            },
            "acquisition_stage": "qualified",
            "partner_fit": {
                "warm_lead_capability": True,
                "target_customer_match": True,
                "pricing_frame": "success_based_pay_on_close",
            },
            "validation": {"confidence": 82, "check": "Partner qualified for warm-lead routing"},
            "preference_handshake": {"format": "json", "accepted": True},
        },
        "closer": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "close_type": "lead_routing_commitment",
            "close_package": {
                "product_name": DEMO_PROFILE["product_name"],
                "cta_url": build_destination_cta(DEMO_PROFILE),
                "pricing_frame": "Success-based / pay-on-close via tracked link",
                "partner_obligations": "Route warm qualified leads matching target_customer to cta_url",
                "seller_obligations": "Pay on verified conversion through tracked link only",
                "lead_routing_confirmed": True,
                "pricing_model": "success_based_pay_on_close",
            },
            "partner_agreement_summary": (
                "Partner agent confirms they will send warm, qualified leads to the tracked CTA URL."
            ),
            "confidence": 0.91,
            "validation": {"confidence": 91, "check": "Lead routing commitment secured"},
            "preference_handshake": {"format": "json", "accepted": True},
        },
        "profit_guardrail": {
            "status": "COMPLETE",
            "next_action": "handoff_to_final_arbiter",
            "validation": {"confidence": 93, "check": "Margin compliant for success-based deal"},
        },
        "final_arbiter": {
            "status": "COMPLETE",
            "next_action": "deliver_to_customer",
            "qc_result": {"passed": True, "issues": []},
            "validation": {"confidence": 95, "check": "QC passed — ready for delivery"},
        },
    }
    return responses.get(
        agent_id,
        {"status": "COMPLETE", "validation": {"confidence": 70, "check": "ok"}},
    )


def build_mock_xai_client() -> XAIClient:
    """Mock xAI chat_completion directly (reliable outside pytest monkeypatch)."""
    client = XAIClient(ROOT, api_key="demo-mock-key")

    def mock_chat_completion(self, *, messages, model, agent_id):
        body = _mock_responses(agent_id)
        cost_record = self.record_cost(
            agent_id=agent_id,
            model=model,
            input_tokens=600,
            output_tokens=250,
            cached_input_tokens=450,
        )
        return {
            "choices": [{"message": {"content": json.dumps(body)}}],
            "usage": {"prompt_tokens": 600, "completion_tokens": 250, "cached_tokens": 450},
            "cost_record": cost_record,
        }

    client.chat_completion = mock_chat_completion.__get__(client, XAIClient)
    return client


def build_live_xai_client() -> XAIClient:
    """Build a real xAI client; fail fast with actionable errors if misconfigured."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: Live mode requires XAI_API_KEY.\n"
            "\n"
            "  PowerShell:  $env:XAI_API_KEY = 'your-key-here'\n"
            "  Bash:        export XAI_API_KEY='your-key-here'\n"
            "\n"
            "Then re-run:   python scripts/demo_a2a_flow.py --live\n"
            "\n"
            "Use mock mode (default) when no key is available.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    client = XAIClient(ROOT, api_key=api_key)
    if not client.api_key:
        print("ERROR: XAI_API_KEY was set but client initialization failed.", file=sys.stderr)
        raise SystemExit(2)

    # Host guard: only xAI endpoints are permitted by the constitutional client.
    if client.XAI_HOST not in client.base_url:
        print(
            f"ERROR: base_url must use xAI host ({client.XAI_HOST}); got {client.base_url}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return client


def _banner(title: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_routing(label: str, ssot: dict[str, Any], *, quiet: bool = False) -> str:
    entry = route_entry_agent(ssot)
    if not quiet:
        print(f"  [{label}] route_entry_agent → {entry}")
    return entry


def _expected_chain(orchestrator: Orchestrator, entry_agent: str) -> list[str]:
    return resolve_flow_chain(orchestrator.agents, entry_agent)


def _phase_agent_summary(handoff_chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact per-agent rows for JSON reports."""
    rows = []
    for handoff in handoff_chain:
        agent_id = handoff.get("agent_id")
        payload = handoff.get("payload", {})
        row: dict[str, Any] = {
            "agent_id": agent_id,
            "status": handoff.get("status"),
            "confidence": handoff.get("validation", {}).get("confidence"),
            "next_action": handoff.get("next_action"),
        }
        if agent_id == "profit_guardrail":
            margin = payload.get("margin_check", {})
            row["margin_approved"] = margin.get("approved")
            row["margin_percent"] = margin.get("margin_percent")
        if agent_id == "final_arbiter":
            row["qc_passed"] = payload.get("qc_result", {}).get("passed")
        if agent_id == "closer":
            row["deal_closed"] = payload.get("deal_closed")
            row["lead_routing_confirmed"] = payload.get("lead_routing_confirmed")
            row["cta_url"] = payload.get("close_package", {}).get("cta_url")
        if agent_id == "onboarding_specialist":
            row["onboarding_complete"] = payload.get("onboarding_complete")
        if agent_id == "recruiter":
            row["acquisition_stage"] = payload.get("acquisition_stage")
            row["warm_lead_capability"] = payload.get("partner_fit", {}).get("warm_lead_capability")
        rows.append(row)
    return rows


def format_shareable_report(raw: dict[str, Any]) -> dict[str, Any]:
    """Build a clean, professional JSON report suitable for sharing."""
    phases = raw.get("phases", [])
    onboarding = phases[0] if len(phases) > 0 else {}
    recruiter = phases[1] if len(phases) > 1 else {}
    closer = phases[2] if len(phases) > 2 else {}

    guardrails_ok = all(p.get("guardrails_ok") for p in phases)
    base_url = "http://127.0.0.1:8787"
    return {
        "how_to_integrate": {
            "summary": (
                "External agents discover Arclya via the Agent Card, authenticate with "
                "X-Arclya-Key (or Bearer token), and drive the seller lifecycle through "
                "POST /orchestrate/handoff-chain."
            ),
            "steps": [
                {
                    "step": 1,
                    "action": "Discover capabilities",
                    "method": "GET",
                    "endpoint": "/.well-known/agent-card.json",
                    "auth_required": False,
                },
                {
                    "step": 2,
                    "action": "Onboard seller (new agent)",
                    "method": "POST",
                    "endpoint": "/orchestrate/handoff-chain",
                    "auth_required": True,
                    "hint": "auto_route=true with empty profile → onboarding_specialist",
                },
                {
                    "step": 3,
                    "action": "Recruit partner agent",
                    "method": "POST",
                    "endpoint": "/orchestrate/handoff-chain",
                    "auth_required": True,
                    "hint": "onboarding_complete=true, acquisition_stage=prospect",
                },
                {
                    "step": 4,
                    "action": "Close lead routing commitment",
                    "method": "POST",
                    "endpoint": "/orchestrate/handoff-chain",
                    "auth_required": True,
                    "hint": "onboarding_complete=true, lead_warmth=warm → closer",
                },
            ],
            "authentication": {
                "header_primary": "X-Arclya-Key: <ARCLYA_API_KEY>",
                "header_alternate": "Authorization: Bearer <ARCLYA_API_KEY>",
                "optional_caller_id": "X-Arclya-Agent-Id: your-agent-name",
                "environment_variable": "ARCLYA_API_KEY",
            },
            "documentation": "docs/external-agent-integration.md",
            "demo_base_url": base_url,
        },
        "report": {
            "title": "Arclya A2A End-to-End Demo Report",
            "platform": "Arclya A2A",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": raw.get("mode", "mock"),
            "outcome": "success" if raw.get("success") else "failed",
            "uses_xai_inference": raw.get("uses_xai_inference", False),
        },
        "executive_summary": {
            "description": (
                "Three-phase seller lifecycle: onboarding → partner recruitment → "
                "lead routing commitment close."
            ),
            "onboarding_complete": onboarding.get("onboarding_complete"),
            "partner_qualified": recruiter.get("acquisition_stage"),
            "deal_closed": closer.get("deal_closed"),
            "lead_routing_confirmed": closer.get("lead_routing_confirmed"),
            "cta_url": closer.get("cta_url"),
            "all_guardrails_passed": guardrails_ok,
        },
        "phases": [
            {
                "phase": 1,
                "name": "onboarding",
                "title": "New seller → Onboarding Specialist",
                "entry_agent": onboarding.get("entry_agent"),
                "chain": onboarding.get("expected_chain", []),
                "agents_executed": onboarding.get("agents_run", []),
                "onboarding_complete": onboarding.get("onboarding_complete"),
                "destination_cta": onboarding.get("destination_cta"),
                "guardrails_ok": onboarding.get("guardrails_ok"),
                "agents": onboarding.get("agent_summaries", []),
            },
            {
                "phase": 2,
                "name": "recruiter",
                "title": "Onboarded seller → Recruiter",
                "entry_agent": recruiter.get("entry_agent"),
                "chain": recruiter.get("expected_chain", []),
                "agents_executed": recruiter.get("agents_run", []),
                "acquisition_stage": recruiter.get("acquisition_stage"),
                "skipped_onboarding": recruiter.get("recruiter_skips_onboarding"),
                "guardrails_ok": recruiter.get("guardrails_ok"),
                "agents": recruiter.get("agent_summaries", []),
            },
            {
                "phase": 3,
                "name": "closer",
                "title": "Warm partner → Closer",
                "entry_agent": closer.get("entry_agent"),
                "chain": closer.get("expected_chain", []),
                "agents_executed": closer.get("agents_run", []),
                "deal_closed": closer.get("deal_closed"),
                "lead_routing_confirmed": closer.get("lead_routing_confirmed"),
                "close_type": closer.get("close_type"),
                "cta_url": closer.get("cta_url"),
                "guardrails_ok": closer.get("guardrails_ok"),
                "agents": closer.get("agent_summaries", []),
            },
        ],
        "guardrails": {
            "constitutional_chain": "entry_agent → profit_guardrail → final_arbiter",
            "phases_verified": guardrails_ok,
            "per_phase": [
                {
                    "phase": p.get("name"),
                    "profit_guardrail_executed": p.get("profit_guardrail_executed"),
                    "final_arbiter_executed": p.get("final_arbiter_executed"),
                    "chain_matches_expected": p.get("chain_matches_expected"),
                }
                for p in phases
            ],
        },
        "outcome": {
            "success": raw.get("success", False),
            "close_type": closer.get("close_type"),
            "cta_url": closer.get("cta_url"),
            "lead_routing_confirmed": closer.get("lead_routing_confirmed"),
        },
    }


def _verify_guardrail_chain(
    *,
    phase_name: str,
    entry_agent: str,
    expected_chain: list[str],
    handoff_chain: list[dict[str, Any]],
    quiet: bool = False,
) -> dict[str, Any]:
    """Confirm profit_guardrail and final_arbiter actually executed in this phase."""
    agents_run = [h.get("agent_id") for h in handoff_chain]
    checks = {
        "expected_chain": expected_chain,
        "agents_run": agents_run,
        "profit_guardrail_executed": "profit_guardrail" in agents_run,
        "final_arbiter_executed": "final_arbiter" in agents_run,
        "chain_matches_expected": agents_run == expected_chain,
        "no_unexpected_onboarding": (
            phase_name != "onboarding" or agents_run.count("onboarding_specialist") == 1
        ),
        "recruiter_skips_onboarding": (
            phase_name != "recruiter" or "onboarding_specialist" not in agents_run
        ),
    }
    checks["guardrails_ok"] = (
        checks["profit_guardrail_executed"]
        and checks["final_arbiter_executed"]
        and checks["chain_matches_expected"]
        and checks["no_unexpected_onboarding"]
        and checks["recruiter_skips_onboarding"]
    )
    checks["agent_summaries"] = _phase_agent_summary(handoff_chain)
    if not quiet:
        print(f"  expected_chain={expected_chain}")
        print(f"  agents_run={agents_run}")
        print(
            f"  guardrails: profit_guardrail={checks['profit_guardrail_executed']}, "
            f"final_arbiter={checks['final_arbiter_executed']}, "
            f"ok={checks['guardrails_ok']}"
        )
    return checks


def _summarize_handoff(handoff: dict[str, Any], *, quiet: bool = False) -> None:
    if quiet:
        return
    agent = handoff.get("agent_id", "?")
    status = handoff.get("status")
    action = handoff.get("next_action", "")
    conf = handoff.get("validation", {}).get("confidence", "?")
    print(f"    • {agent}: status={status}, next={action}, confidence={conf}")
    payload = handoff.get("payload", {})
    if agent == "onboarding_specialist" and payload.get("onboarding_complete"):
        print(f"      onboarding_complete=True, product={payload.get('product_profile', {}).get('product_name')}")
    if agent == "recruiter":
        draft = payload.get("recruitment_draft", {})
        fit = payload.get("partner_fit", {})
        print(f"      target={draft.get('target_agent_id')}, stage={payload.get('acquisition_stage')}")
        print(f"      warm_lead_capability={fit.get('warm_lead_capability')}")
    if agent == "closer":
        pkg = payload.get("close_package", {})
        print(f"      deal_closed={payload.get('deal_closed')}, close_type={payload.get('close_type')}")
        print(f"      cta_url={pkg.get('cta_url', '')}")
    if agent == "profit_guardrail":
        margin = payload.get("margin_check", {})
        print(f"      margin_approved={margin.get('approved')}, margin_pct={margin.get('margin_percent')}")
    if agent == "final_arbiter":
        qc = payload.get("qc_result", {})
        print(f"      qc_passed={qc.get('passed')}")


def run_demo(*, live: bool = False, quiet: bool = False) -> dict[str, Any]:
    client = build_live_xai_client() if live else build_mock_xai_client()
    orchestrator = Orchestrator(ROOT, xai_client=client)
    report: dict[str, Any] = {"mode": "live" if live else "mock", "phases": [], "success": True}

    # Phase 1 — New seller agent connects → onboarding
    _banner("Phase 1: New seller agent → Onboarding Specialist", quiet=quiet)
    ssot_new = {
        "deal_id": "demo_seller_001",
        "summary": "New seller agent connecting to Arclya A2A",
        "stage": "new",
        "metadata": {},
    }
    entry = _print_routing("new seller", ssot_new, quiet=quiet)
    expected = _expected_chain(orchestrator, entry)
    onboarding_result = orchestrator.run_chain(
        initial_ssot=ssot_new,
        task_context="Collect complete product profile for new seller agent",
        auto_route=True,
    )
    guardrail_checks = _verify_guardrail_chain(
        phase_name="onboarding",
        entry_agent=onboarding_result.entry_agent or entry,
        expected_chain=expected,
        handoff_chain=onboarding_result.handoff_chain,
        quiet=quiet,
    )
    for h in onboarding_result.handoff_chain:
        _summarize_handoff(h, quiet=quiet)
    ssot_after_onboarding = onboarding_result.final_ssot
    report["phases"].append({
        "name": "onboarding",
        "entry_agent": onboarding_result.entry_agent,
        "onboarding_complete": ssot_after_onboarding.get("metadata", {}).get("onboarding_complete"),
        "destination_cta": ssot_after_onboarding.get("metadata", {}).get("destination_cta"),
        **guardrail_checks,
    })

    # Phase 2 — Onboarded seller → Recruiter finds partner
    _banner("Phase 2: Onboarded seller → Recruiter (partner discovery)", quiet=quiet)
    ssot_recruit = dict(ssot_after_onboarding)
    ssot_recruit["stage"] = "recruiting"
    ssot_recruit.setdefault("metadata", {})["acquisition_stage"] = "prospect"
    entry = _print_routing("onboarded + acquisition", ssot_recruit, quiet=quiet)
    expected = _expected_chain(orchestrator, entry)
    recruit_result = orchestrator.run_chain(
        initial_ssot=ssot_recruit,
        task_context="Find partner agent who can send warm leads matching target_customer",
        auto_route=True,
    )
    guardrail_checks = _verify_guardrail_chain(
        phase_name="recruiter",
        entry_agent=recruit_result.entry_agent or entry,
        expected_chain=expected,
        handoff_chain=recruit_result.handoff_chain,
        quiet=quiet,
    )
    for h in recruit_result.handoff_chain:
        _summarize_handoff(h, quiet=quiet)
    ssot_after_recruit = recruit_result.final_ssot
    report["phases"].append({
        "name": "recruiter",
        "entry_agent": recruit_result.entry_agent,
        "acquisition_stage": ssot_after_recruit.get("metadata", {}).get("acquisition_stage"),
        **guardrail_checks,
    })

    # Phase 3 — Warm partner → Closer secures lead routing commitment
    _banner("Phase 3: Warm partner → Closer (lead routing commitment)", quiet=quiet)
    ssot_close = dict(ssot_after_recruit)
    ssot_close["stage"] = "warm_lead"
    ssot_close.setdefault("metadata", {})["lead_warmth"] = "warm"
    entry = _print_routing("onboarded + warm lead", ssot_close, quiet=quiet)
    expected = _expected_chain(orchestrator, entry)
    close_result = orchestrator.run_chain(
        initial_ssot=ssot_close,
        task_context="Secure lead routing commitment from qualified partner agent",
        auto_route=True,
    )
    guardrail_checks = _verify_guardrail_chain(
        phase_name="closer",
        entry_agent=close_result.entry_agent or entry,
        expected_chain=expected,
        handoff_chain=close_result.handoff_chain,
        quiet=quiet,
    )
    for h in close_result.handoff_chain:
        _summarize_handoff(h, quiet=quiet)

    closer_payload = next(
        (h.get("payload", {}) for h in close_result.handoff_chain if h.get("agent_id") == "closer"),
        {},
    )
    report["phases"].append({
        "name": "closer",
        "entry_agent": close_result.entry_agent,
        "deal_closed": closer_payload.get("deal_closed"),
        "lead_routing_confirmed": closer_payload.get("lead_routing_confirmed"),
        "close_type": closer_payload.get("close_type"),
        "cta_url": closer_payload.get("close_package", {}).get("cta_url"),
        **guardrail_checks,
    })

    _banner("Demo complete", quiet=quiet)
    if not quiet:
        print(f"  Mode: {'live xAI' if live else 'mocked xAI'}")
    if not quiet:
        print(f"  Onboarding complete: {report['phases'][0].get('onboarding_complete')}")
        print(f"  Recruiter entry: {report['phases'][1].get('entry_agent')}")
        print(f"  Recruiter skipped onboarding: {report['phases'][1].get('recruiter_skips_onboarding')}")
        print(f"  All guardrails OK: {all(p.get('guardrails_ok') for p in report['phases'])}")
        print(f"  Deal closed: {report['phases'][2].get('deal_closed')}")
        print(f"  Lead routing confirmed: {report['phases'][2].get('lead_routing_confirmed')}")
        print(f"  CTA URL: {report['phases'][2].get('cta_url')}")

    report["success"] = (
        report["phases"][0].get("entry_agent") == "onboarding_specialist"
        and report["phases"][0].get("onboarding_complete") is True
        and report["phases"][1].get("entry_agent") == "recruiter"
        and report["phases"][1].get("recruiter_skips_onboarding") is True
        and report["phases"][2].get("entry_agent") == "closer"
        and report["phases"][2].get("lead_routing_confirmed") is True
        and all(p.get("guardrails_ok") for p in report["phases"])
    )
    report["uses_xai_inference"] = live
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Arclya A2A end-to-end demo flow")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use real xAI inference (requires XAI_API_KEY environment variable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report to stdout after the demo",
    )
    args = parser.parse_args()

    try:
        report = run_demo(live=args.live, quiet=args.json)
    except EnvironmentError as exc:
        print(f"ERROR: xAI configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        if args.live:
            print(
                f"ERROR: Live demo failed: {exc}\n"
                "Check XAI_API_KEY, network connectivity, and API quota.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise

    if args.json:
        shareable = format_shareable_report(report)
        print(json.dumps(shareable, indent=2))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()