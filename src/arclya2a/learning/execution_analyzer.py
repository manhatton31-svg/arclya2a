"""Analyze tool executions, billing, and demo outcomes for Meta Optimizer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.billing.tracker import billing_summary, list_closed_deals
from arclya2a.tools.observability import execution_summary, list_tool_executions

PROMPT_TARGETS = {
    "onboarding": "prompts/onboarding_prompt.md",
    "recruiter": "prompts/recruiter_prompt.md",
    "closer": "prompts/closer_prompt.md",
    "outreach_worker": "prompts/outreach_worker.md",
}


def analyze_tool_executions(root: Path, *, limit: int = 100) -> dict[str, Any]:
    """Summarize tool execution success/failure from observability log."""
    rows = list_tool_executions(root, limit=limit)
    summary = execution_summary(root, limit=limit)
    if not rows:
        return {
            "total": 0,
            "success_rate": 0.0,
            "failure_rate": 0.0,
            "skipped_rate": 0.0,
            "by_tool": {},
            "by_agent": {},
            "error_codes": {},
            "issues": [],
            "recommendations": [],
        }

    total = len(rows)
    successes = sum(1 for r in rows if r.get("outcome") in ("success", "dry_run"))
    failures = sum(1 for r in rows if r.get("outcome") == "failed")
    skipped = sum(1 for r in rows if r.get("outcome") == "skipped")

    by_tool: dict[str, dict[str, int]] = {}
    by_agent: dict[str, dict[str, int]] = {}
    error_codes: dict[str, int] = {}

    for row in rows:
        tool_id = row.get("tool_id", "unknown")
        agent_id = row.get("agent_id", "unknown")
        outcome = row.get("outcome", "unknown")
        by_tool.setdefault(tool_id, {}).setdefault(outcome, 0)
        by_tool[tool_id][outcome] += 1
        by_agent.setdefault(agent_id, {}).setdefault(outcome, 0)
        by_agent[agent_id][outcome] += 1
        if row.get("error_code"):
            error_codes[row["error_code"]] = error_codes.get(row["error_code"], 0) + 1

    failure_rate = failures / total if total else 0.0
    issues: list[str] = []
    recommendations: list[str] = []

    if failure_rate > 0.2:
        issues.append("tool_high_failure_rate")
        recommendations.append(
            f"Tool failure rate {failure_rate:.0%} — review connector credentials and Closer tool judgment rules"
        )
    if skipped / total > 0.3 if total else False:
        issues.append("tool_high_skip_rate")
        recommendations.append(
            "Many tool calls skipped (missing credentials) — ensure ARCLYA_TOOL_DRY_RUN or configure connectors"
        )
    if error_codes.get("rate_limited", 0) >= 2:
        issues.append("tool_rate_limited")
        recommendations.append(
            "Repeated rate_limit errors — reduce tool call frequency in Closer prompt; batch follow-ups"
        )

    closer_rows = [r for r in rows if r.get("agent_id") == "closer"]
    gmail_before_close = [
        r for r in closer_rows
        if r.get("tool_id") == "gmail.send_followup_email"
        and "mid-negotiation" in (r.get("reason") or "").lower()
    ]
    if gmail_before_close:
        issues.append("tools_called_too_early")
        recommendations.append(
            "Closer called Gmail before deal close — strengthen 'When you should NOT call tools' gate"
        )

    return {
        "total": total,
        "success_rate": round(successes / total, 4) if total else 0.0,
        "failure_rate": round(failure_rate, 4),
        "skipped_rate": round(skipped / total, 4) if total else 0.0,
        "summary": summary,
        "by_tool": by_tool,
        "by_agent": by_agent,
        "error_codes": error_codes,
        "issues": issues,
        "recommendations": recommendations,
    }


def analyze_billing_data(root: Path, *, limit: int = 50) -> dict[str, Any]:
    """Analyze closed deal billing records."""
    deals = list_closed_deals(root, limit=limit)
    summary = billing_summary(root)
    issues: list[str] = []
    recommendations: list[str] = []

    if not deals:
        issues.append("billing_no_deals")
        recommendations.append(
            "No closed deals recorded — verify Closer sets deal_closed + lead_routing_confirmed for billing trigger"
        )
    else:
        margins = [float(d.get("margin_percent", 0)) for d in deals]
        avg_margin = sum(margins) / len(margins) if margins else 0
        if avg_margin < 15:
            issues.append("billing_low_margin")
            recommendations.append(
                f"Average margin {avg_margin:.1f}% below threshold — review profit_guardrail and deal pricing"
            )
        missing_affiliate = [d for d in deals if not d.get("affiliate_code")]
        if missing_affiliate:
            issues.append("billing_missing_attribution")
            recommendations.append(
                "Closed deals missing affiliate_code — Closer must construct tracked cta_url from profile"
            )

    return {
        "deal_count": summary.get("deal_count", 0),
        "total_revenue_usd": summary.get("total_revenue_usd", 0),
        "average_margin_percent": summary.get("average_margin_percent"),
        "recent_deals": deals[:5],
        "issues": issues,
        "recommendations": recommendations,
    }


def analyze_demo_phases(report: dict[str, Any]) -> dict[str, Any]:
    """Deep analysis of demo phase outcomes including tools and negotiation."""
    issues: list[str] = []
    recommendations: list[str] = []
    prompt_targets: list[str] = []

    phases = {p.get("name"): p for p in report.get("phases", []) if p.get("name")}
    closer = phases.get("closer", {})
    recruiter = phases.get("recruiter", {})
    onboarding = phases.get("onboarding", {})

    phase_results = {}
    for name, phase in phases.items():
        ok = phase.get("guardrails_ok", True) and phase.get("chain_matches_expected", True)
        phase_results[name] = {
            "success": ok,
            "entry_agent": phase.get("entry_agent"),
            "tools_executed": phase.get("tools_executed", 0),
            "tool_results": phase.get("tool_results", []),
        }

    if not onboarding.get("onboarding_complete"):
        issues.append("onboarding_incomplete")
        prompt_targets.append(PROMPT_TARGETS["onboarding"])

    if closer:
        if not closer.get("deal_closed") or not closer.get("lead_routing_confirmed"):
            issues.append("closer_no_commitment")
            recommendations.append(
                "Closer failed to secure lead routing commitment — add objection playbook turns"
            )
            prompt_targets.append(PROMPT_TARGETS["closer"])

        tool_results = closer.get("tool_results") or []
        failed_tools = [t for t in tool_results if t.get("outcome") == "failed"]
        if failed_tools:
            issues.append("demo_tool_failures")
            recommendations.append(
                f"Demo closer had {len(failed_tools)} failed tool(s) — review tool parameters and credentials"
            )
            prompt_targets.append(PROMPT_TARGETS["closer"])

        if closer.get("deal_closed") and not tool_results:
            issues.append("demo_no_tools_on_close")
            recommendations.append(
                "Deal closed but no tools executed — Closer should request linear.create_followup_task on close"
            )
            prompt_targets.append(PROMPT_TARGETS["closer"])

        if not closer.get("deal_closed") and tool_results:
            issues.append("tools_called_before_close")
            recommendations.append(
                "Tools called before deal closed — Closer must not call external tools mid-negotiation"
            )
            prompt_targets.append(PROMPT_TARGETS["closer"])

    if recruiter and not recruiter.get("recruiter_skips_onboarding", True):
        issues.append("recruiter_retriggered_onboarding")
        prompt_targets.append(PROMPT_TARGETS["recruiter"])

    return {
        "phase_results": phase_results,
        "issues": issues,
        "recommendations": recommendations,
        "prompt_targets": prompt_targets,
    }


def analyze_negotiation_effectiveness(report: dict[str, Any]) -> dict[str, Any]:
    """Analyze negotiation length and objection handling from closer payload."""
    issues: list[str] = []
    recommendations: list[str] = []

    closer_agents = []
    for phase in report.get("phases", []):
        if phase.get("name") == "closer":
            for agent in phase.get("agents", []) or phase.get("agent_summaries", []):
                if agent.get("agent_id") == "closer":
                    closer_agents.append(agent)

    negotiation_turns = None
    objections_handled: list[str] = []
    for phase in report.get("phases", []):
        if phase.get("name") != "closer":
            continue
        for agent in phase.get("agents", []):
            if agent.get("agent_id") == "closer":
                payload = agent  # summaries may not have full payload
                negotiation_turns = payload.get("negotiation_turns")

    exec_summary = report.get("executive_summary", {})
    if negotiation_turns is None:
        negotiation_turns = report.get("negotiation_turns")

    if negotiation_turns is not None and negotiation_turns < 3:
        if not exec_summary.get("lead_routing_confirmed"):
            issues.append("negotiation_too_short")
            recommendations.append(
                f"Negotiation only {negotiation_turns} turns — require qualify + terms + confirm protocol"
            )

    objections = report.get("objections_handled") or []
    if isinstance(objections, list) and len(objections) == 0:
        if not exec_summary.get("lead_routing_confirmed"):
            issues.append("objections_not_documented")
            recommendations.append(
                "Objections not documented in objections_handled — map to common_objections playbook"
            )

    return {
        "negotiation_turns": negotiation_turns,
        "objections_handled": objections,
        "issues": issues,
        "recommendations": recommendations,
    }


def build_execution_learning_context(
    root: Path,
    demo_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate all execution data sources for Meta Optimizer."""
    tool_analysis = analyze_tool_executions(root)
    billing_analysis = analyze_billing_data(root)
    demo_analysis = analyze_demo_phases(demo_report) if demo_report else {"issues": [], "recommendations": [], "prompt_targets": [], "phase_results": {}}
    negotiation_analysis = analyze_negotiation_effectiveness(demo_report) if demo_report else {"issues": [], "recommendations": []}

    all_issues = list(dict.fromkeys(
        tool_analysis.get("issues", [])
        + billing_analysis.get("issues", [])
        + demo_analysis.get("issues", [])
        + negotiation_analysis.get("issues", [])
    ))
    all_recs = list(dict.fromkeys(
        tool_analysis.get("recommendations", [])
        + billing_analysis.get("recommendations", [])
        + demo_analysis.get("recommendations", [])
        + negotiation_analysis.get("recommendations", [])
    ))
    prompt_targets = list(dict.fromkeys(demo_analysis.get("prompt_targets", [])))

    priority = "low"
    if any(i in all_issues for i in (
        "closer_no_commitment", "tools_called_before_close", "tool_high_failure_rate",
        "onboarding_incomplete", "demo_tool_failures",
    )):
        priority = "high"
    elif all_issues:
        priority = "medium"

    weakest_phase = "none"
    if any("closer" in i or "tool" in i or "negotiation" in i for i in all_issues):
        weakest_phase = "closer"
    elif any("recruiter" in i for i in all_issues):
        weakest_phase = "recruiter"
    elif "onboarding_incomplete" in all_issues:
        weakest_phase = "onboarding"
    elif any("billing" in i for i in all_issues):
        weakest_phase = "closer"

    primary_target = prompt_targets[0] if prompt_targets else PROMPT_TARGETS["closer"]

    raw_signal = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "source": "execution_data",
        "agent_id": "platform",
        "tool_executions": tool_analysis,
        "billing": billing_analysis,
        "demo_phases": demo_analysis.get("phase_results", {}),
        "negotiation": negotiation_analysis,
        "issues_detected": all_issues,
        "recommendations": all_recs,
        "prompt_targets": prompt_targets or [primary_target],
        "meta_optimizer_target": primary_target,
        "weakest_phase": weakest_phase,
        "priority": priority,
        "demo_success": demo_report.get("success") if demo_report else None,
    }

    from arclya2a.security.cross_agent_isolation import apply_learning_signal_isolation

    return apply_learning_signal_isolation(raw_signal)


def emit_execution_learning_signal(
    root: Path,
    demo_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build execution context, persist signal, return improvement payload."""
    signal = build_execution_learning_context(root, demo_report)
    from arclya2a.learning.patch_outcomes import evaluate_patch_outcomes

    evaluate_patch_outcomes(root, signal.get("issues_detected", []), signal=signal)
    learning_dir = root / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    out_path = learning_dir / "execution_signals.jsonl"
    entry = {
        "timestamp": signal["analyzed_at"],
        "improvement_signal": signal,
    }
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return signal


def load_latest_execution_signal(root: Path) -> dict[str, Any] | None:
    path = root / "learning" / "execution_signals.jsonl"
    if not path.exists():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        latest = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return latest.get("improvement_signal") or latest