"""Analyze demo flow outcomes and emit prompt improvement signals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROMPT_TARGETS = {
    "onboarding": "prompts/onboarding_prompt.md",
    "recruiter": "prompts/recruiter_prompt.md",
    "closer": "prompts/closer_prompt.md",
    "outreach_worker": "prompts/outreach_worker.md",
}


def _phase_by_name(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    for phase in report.get("phases", []):
        if phase.get("name") == name or phase.get("phase") == name:
            return phase
    return None


def analyze_demo_report(report: dict[str, Any]) -> dict[str, Any]:
    """Derive structured improvement recommendations from a demo report."""
    recommendations: list[str] = []
    prompt_targets: list[str] = []
    issues: list[str] = []
    priority = "low"

    exec_summary = report.get("executive_summary", {})
    guardrails = report.get("guardrails", {})
    outcome = report.get("outcome", {})

    onboarding = _phase_by_name(report, "onboarding") or {}
    recruiter = _phase_by_name(report, "recruiter") or {}
    closer = _phase_by_name(report, "closer") or {}

    if not exec_summary.get("onboarding_complete"):
        issues.append("onboarding_incomplete")
        recommendations.append(
            "Strengthen onboarding prompt: enforce all product_profile fields before handoff"
        )
        prompt_targets.append(PROMPT_TARGETS["onboarding"])
        priority = "high"

    if recruiter and not recruiter.get("skipped_onboarding", True):
        issues.append("recruiter_retriggered_onboarding")
        recommendations.append(
            "Recruiter prompt: clarify that onboarded sellers must route to profit_guardrail, never onboarding"
        )
        prompt_targets.append(PROMPT_TARGETS["recruiter"])
        priority = "high"

    if recruiter and recruiter.get("acquisition_stage") not in ("qualified", "invited", "prospect", "recruiting"):
        issues.append("recruiter_weak_qualification")
        recommendations.append(
            "Recruiter prompt: require explicit warm_lead_capability and target_customer_match before qualified stage"
        )
        prompt_targets.append(PROMPT_TARGETS["recruiter"])
        if priority != "high":
            priority = "medium"

    if not closer.get("deal_closed") or not closer.get("lead_routing_confirmed"):
        issues.append("closer_no_commitment")
        recommendations.append(
            "Closer prompt: add objection-handling turns and require explicit warm-lead routing confirmation"
        )
        recommendations.append(
            "Closer prompt: reject vague interest ('will consider') — demand tracked CTA commitment"
        )
        prompt_targets.append(PROMPT_TARGETS["closer"])
        priority = "high"

    if closer.get("deal_closed") and not closer.get("cta_url"):
        issues.append("closer_missing_cta")
        recommendations.append("Closer prompt: always construct cta_url from destination_link + affiliate_code")
        prompt_targets.append(PROMPT_TARGETS["closer"])
        priority = "high"

    if not guardrails.get("phases_verified", True):
        issues.append("guardrail_chain_broken")
        recommendations.append(
            "Verify constitutional chain entry_agent → profit_guardrail → final_arbiter on every phase"
        )
        priority = "high"

    for phase_check in guardrails.get("per_phase", []):
        if not phase_check.get("chain_matches_expected"):
            phase_name = phase_check.get("phase", "unknown")
            recommendations.append(f"Phase {phase_name}: registry handoff_targets may be misconfigured")
            priority = "high"

    closer_tools = closer.get("tool_results") or []
    if closer.get("deal_closed") and not closer_tools:
        issues.append("demo_no_tools_on_close")
        recommendations.append(
            "Deal closed without tool follow-up — Closer should request linear.create_followup_task"
        )
        prompt_targets.append(PROMPT_TARGETS["closer"])
        if priority != "high":
            priority = "medium"

    if not closer.get("deal_closed") and closer_tools:
        issues.append("tools_called_before_close")
        recommendations.append(
            "Tools executed before deal closed — forbid external tool calls mid-negotiation"
        )
        prompt_targets.append(PROMPT_TARGETS["closer"])
        priority = "high"

    failed_tools = [t for t in closer_tools if t.get("outcome") == "failed"]
    if failed_tools:
        issues.append("demo_tool_failures")
        recommendations.append(f"Closer had {len(failed_tools)} failed tool execution(s) in demo")
        prompt_targets.append(PROMPT_TARGETS["closer"])
        priority = "high"

    if not recommendations:
        recommendations.append(
            "Demo passed all phases; reinforce success-based framing and warm-lead qualification language"
        )
        prompt_targets.append(PROMPT_TARGETS["closer"])
        priority = "low"

    primary_target = prompt_targets[0] if prompt_targets else PROMPT_TARGETS["outreach_worker"]
    unique_targets = list(dict.fromkeys(prompt_targets))

    weakest_phase = "none"
    if "closer_no_commitment" in issues or "closer_missing_cta" in issues:
        weakest_phase = "closer"
    elif "recruiter_retriggered_onboarding" in issues or "recruiter_weak_qualification" in issues:
        weakest_phase = "recruiter"
    elif "onboarding_incomplete" in issues:
        weakest_phase = "onboarding"
    elif "guardrail_chain_broken" in issues:
        weakest_phase = "guardrails"

    return {
        "source": "demo_outcomes",
        "demo_success": bool(outcome.get("success", report.get("success"))),
        "issues_detected": issues,
        "recommendations": recommendations,
        "prompt_targets": unique_targets,
        "meta_optimizer_target": primary_target,
        "weakest_phase": weakest_phase,
        "priority": priority,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def emit_demo_learning_signal(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Analyze demo report, persist signal, return improvement payload for meta optimizer."""
    signal = analyze_demo_report(report)
    learning_dir = root / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    out_path = learning_dir / "demo_outcomes.jsonl"
    entry = {
        "timestamp": signal["analyzed_at"],
        "demo_success": signal["demo_success"],
        "issues": signal["issues_detected"],
        "improvement_signal": signal,
    }
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return signal


def load_latest_demo_signal(root: Path) -> dict[str, Any] | None:
    """Load most recent demo outcome improvement signal."""
    path = root / "learning" / "demo_outcomes.jsonl"
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