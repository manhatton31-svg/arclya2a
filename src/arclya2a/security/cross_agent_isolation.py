"""Cross-agent isolation: prevent one partner/agent from poisoning shared learning."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

SCOPE_SANDBOX = "sandbox"
SCOPE_PRODUCTION = "production"
SCOPE_PLATFORM = "platform"

# Issues that must never promote to production-global patches from sandbox traffic alone.
SANDBOX_ONLY_ISSUES = frozenset({
    "sandbox_suspicious_partner",
    "sandbox_repeat_offender",
    "sandbox_tool_block",
})

# Issues attributed to a single external partner must not drive broad patches alone.
PARTNER_SCOPED_ISSUES = frozenset({
    "high_risk_partner",
    "sandbox_repeat_offender",
})

# Shared prompt / scanner targets affected by defensive patches.
BROAD_IMPACT_TARGETS = frozenset({
    "prompts/closer_prompt.md",
    "prompts/onboarding_prompt.md",
    "prompts/recruiter_prompt.md",
    "prompts/outreach_worker.md",
    "learning/injection_patterns.json",
})

ISSUE_TO_RECOMMENDATION_PREFIX = {
    "injection_scan_rejection": "injection scan blocks",
    "injection_scan_disqualify": "closer disqualifications",
    "repeated_injection_pattern": "Pattern",
    "tool_gate_violation": "tool gate blocks",
    "tool_gate_partner_command": "Partner-commanded",
    "tool_gate_premature": "Tools requested before",
    "sandbox_suspicious_partner": "sandbox security events",
    "sandbox_repeat_offender": "Repeat sandbox offenders",
    "emergency_stop_security": "EMERGENCY_STOP events",
    "high_risk_partner": "Partners",
    "suspicious_partner_trust_block": "Suspicious trust",
    "sandbox_tool_block": "Sandbox high-risk",
}


def min_distinct_actors_for_global_patch() -> int:
    raw = os.environ.get("ARCLYA_ISOLATION_MIN_ACTORS", "2").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 2


@dataclass
class IsolationCheckResult:
    allowed: bool
    reason: str
    broad_impact: bool
    isolation_scope: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "broad_impact": self.broad_impact,
            "isolation_scope": self.isolation_scope,
        }


def resolve_isolation_scope(
    *,
    partner_id: str | None = None,
    sandbox_mode: bool = False,
) -> str:
    """Return isolation scope for an incident or orchestration context."""
    if sandbox_mode or (partner_id and partner_id.startswith("tp_")):
        return SCOPE_SANDBOX
    if partner_id:
        return SCOPE_PRODUCTION
    return SCOPE_PLATFORM


def tag_incident(
    incident: dict[str, Any],
    *,
    partner_id: str | None = None,
    seller_agent_id: str | None = None,
    sandbox_mode: bool = False,
) -> dict[str, Any]:
    """Attach actor tags and isolation scope to a learning/security incident."""
    tagged = dict(incident)
    tagged["partner_id"] = partner_id or incident.get("partner_id")
    tagged["seller_agent_id"] = seller_agent_id or incident.get("seller_agent_id")
    tagged["isolation_scope"] = resolve_isolation_scope(
        partner_id=tagged.get("partner_id"),
        sandbox_mode=sandbox_mode or incident.get("sandbox_mode", False),
    )
    return tagged


def enrich_orchestrator_context(
    context: dict[str, Any],
    ssot: dict[str, Any],
    *,
    partner_id: str | None = None,
    sandbox_mode: bool = False,
) -> dict[str, Any]:
    """Inject isolation fields into orchestrator agent context."""
    meta = ssot.get("metadata") or {}
    profile = meta.get("product_profile") or {}
    seller_agent_id = profile.get("agent_name") or meta.get("seller_agent_id")
    scope = resolve_isolation_scope(partner_id=partner_id, sandbox_mode=sandbox_mode)

    enriched = dict(context)
    enriched["partner_id"] = partner_id
    enriched["sandbox_mode"] = sandbox_mode
    enriched["seller_agent_id"] = seller_agent_id
    enriched["isolation_scope"] = scope
    enriched["isolation"] = {
        "scope": scope,
        "partner_id": partner_id,
        "seller_agent_id": seller_agent_id,
        "sandbox_mode": sandbox_mode,
    }
    return enriched


def _collect_partner_ids(signal: dict[str, Any]) -> list[str]:
    partners: set[str] = set()
    for key in ("partner_id",):
        if signal.get(key):
            partners.add(str(signal[key]))

    injection = signal.get("injection_scans") or {}
    for pid in (injection.get("by_partner") or {}):
        if pid:
            partners.add(str(pid))

    sandbox = signal.get("sandbox_events") or {}
    for pid in (sandbox.get("by_partner") or {}):
        if pid:
            partners.add(str(pid))

    for bucket in signal.get("by_partner", {}).values():
        if isinstance(bucket, dict) and bucket.get("partner_id"):
            partners.add(str(bucket["partner_id"]))

    return sorted(partners)


def _actors_for_issue(signal: dict[str, Any], issue: str) -> list[str]:
    """Return partner_ids attributed to a given issue."""
    if issue == "high_risk_partner":
        injection = signal.get("injection_scans") or {}
        by_partner = injection.get("by_partner") or {}
        threshold = 2
        return sorted(pid for pid, count in by_partner.items() if pid and count >= threshold)

    if issue in ("sandbox_repeat_offender", "sandbox_suspicious_partner"):
        sandbox = signal.get("sandbox_events") or {}
        by_partner = sandbox.get("by_partner") or {}
        threshold = 2 if issue == "sandbox_repeat_offender" else 1
        return sorted(pid for pid, count in by_partner.items() if pid and count >= threshold)

    partners = _collect_partner_ids(signal)
    return partners


def _is_sandbox_dominant(signal: dict[str, Any]) -> bool:
    sandbox = signal.get("sandbox_events") or {}
    if sandbox.get("suspicious_events", 0) > 0:
        return True
    isolation = signal.get("isolation") or {}
    return isolation.get("scope") == SCOPE_SANDBOX


def _filter_recommendations(issues: list[str], recommendations: list[str]) -> list[str]:
    if not issues:
        return []
    kept: list[str] = []
    issue_set = set(issues)
    for rec in recommendations:
        lower = rec.lower()
        if any(
            ISSUE_TO_RECOMMENDATION_PREFIX.get(issue, "").lower() in lower
            for issue in issue_set
            if ISSUE_TO_RECOMMENDATION_PREFIX.get(issue)
        ):
            kept.append(rec)
    if kept:
        return list(dict.fromkeys(kept))
    return recommendations[: len(issues)] if issues else []


def _build_by_partner(signal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-partner issue breakdown for scoped learning (not shared patches)."""
    by_partner: dict[str, dict[str, Any]] = {}

    injection = signal.get("injection_scans") or {}
    for pid, count in (injection.get("by_partner") or {}).items():
        if not pid:
            continue
        bucket = by_partner.setdefault(pid, {"partner_id": pid, "issues": [], "incident_count": 0})
        bucket["incident_count"] += count
        if count >= 2:
            bucket["issues"].append("high_risk_partner")

    sandbox = signal.get("sandbox_events") or {}
    for pid, count in (sandbox.get("by_partner") or {}).items():
        if not pid:
            continue
        bucket = by_partner.setdefault(pid, {"partner_id": pid, "issues": [], "incident_count": 0})
        bucket["incident_count"] += count
        if count >= 2:
            bucket["issues"].append("sandbox_repeat_offender")
        if count >= 1:
            bucket["issues"].append("sandbox_suspicious_partner")

    for pid, bucket in by_partner.items():
        bucket["issues"] = list(dict.fromkeys(bucket["issues"]))
        bucket["isolation_scope"] = SCOPE_SANDBOX if pid.startswith("tp_") else SCOPE_PRODUCTION
    return by_partner


def apply_learning_signal_isolation(signal: dict[str, Any]) -> dict[str, Any]:
    """
    Filter global issues/recommendations so one bad actor cannot poison shared learning.

    - Sandbox-only issues never promote to production-global patches.
    - Partner-scoped issues require MIN_DISTINCT_ACTORS unrelated partners.
    - Per-partner breakdown preserved in `by_partner` for scoped review.
    """
    scoped = dict(signal)
    min_actors = min_distinct_actors_for_global_patch()
    all_issues = list(scoped.get("issues_detected") or [])
    all_recs = list(scoped.get("recommendations") or [])

    global_issues: list[str] = []
    partner_scoped_issues: dict[str, list[str]] = {}
    sandbox_isolated_issues: list[str] = []
    excluded_issues: list[str] = []
    excluded_reasons: dict[str, str] = {}

    for issue in all_issues:
        if issue in SANDBOX_ONLY_ISSUES:
            sandbox_isolated_issues.append(issue)
            excluded_issues.append(issue)
            excluded_reasons[issue] = "sandbox_isolated_from_production"
            continue

        if issue in PARTNER_SCOPED_ISSUES:
            actors = _actors_for_issue(scoped, issue)
            if len(actors) < min_actors:
                for actor in actors:
                    partner_scoped_issues.setdefault(actor, []).append(issue)
                excluded_issues.append(issue)
                excluded_reasons[issue] = (
                    f"single_partner_attribution ({len(actors)} actor(s), need {min_actors})"
                )
                continue

        global_issues.append(issue)

    scoped["issues_detected"] = global_issues
    scoped["recommendations"] = _filter_recommendations(global_issues, all_recs)
    scoped["by_partner"] = _build_by_partner(scoped)

    partner_ids = _collect_partner_ids(scoped)
    sandbox_dominant = _is_sandbox_dominant(scoped) or bool(sandbox_isolated_issues)
    allows_global = bool(global_issues)

    scoped["isolation"] = {
        "scope": SCOPE_SANDBOX if sandbox_dominant and not global_issues else SCOPE_PLATFORM,
        "attributed_partners": partner_ids,
        "distinct_partner_count": len(partner_ids),
        "allows_global_patch": allows_global,
        "sandbox_isolated_from_production": bool(sandbox_isolated_issues),
        "partner_scoped_issues": partner_scoped_issues,
        "sandbox_isolated_issues": sandbox_isolated_issues,
        "excluded_issues": excluded_issues,
        "excluded_reasons": excluded_reasons,
        "min_actors_for_global_patch": min_actors,
    }
    return scoped


def is_broad_impact_patch(patch: dict[str, Any]) -> bool:
    target = patch.get("target_prompt", "")
    if patch.get("patch_kind") == "injection_pattern":
        return True
    return target in BROAD_IMPACT_TARGETS


def check_patch_isolation(
    patch: dict[str, Any],
    signal: dict[str, Any] | None = None,
) -> IsolationCheckResult:
    """Verify a patch may be applied without cross-agent poisoning."""
    evidence = patch.get("evidence") or {}
    isolation = (signal or {}).get("isolation") or evidence.get("isolation") or {}
    scope = isolation.get("scope", SCOPE_PLATFORM)
    broad = is_broad_impact_patch(patch)
    issue = patch.get("issue", "")

    if not broad:
        return IsolationCheckResult(
            allowed=True,
            reason="narrow_impact_patch",
            broad_impact=False,
            isolation_scope=scope,
        )

    if issue in SANDBOX_ONLY_ISSUES:
        return IsolationCheckResult(
            allowed=False,
            reason="sandbox_only_issue_blocked_from_production_prompts",
            broad_impact=True,
            isolation_scope=SCOPE_SANDBOX,
        )

    if not isolation.get("allows_global_patch", True):
        return IsolationCheckResult(
            allowed=False,
            reason="signal_not_eligible_for_global_patch",
            broad_impact=True,
            isolation_scope=scope,
        )

    if issue in PARTNER_SCOPED_ISSUES:
        actors = isolation.get("attributed_partners") or []
        min_actors = isolation.get("min_actors_for_global_patch", min_distinct_actors_for_global_patch())
        if len(actors) < min_actors:
            return IsolationCheckResult(
                allowed=False,
                reason=f"partner_scoped_issue_needs_{min_actors}_actors",
                broad_impact=True,
                isolation_scope=scope,
            )

    return IsolationCheckResult(
        allowed=True,
        reason="isolation_check_passed",
        broad_impact=broad,
        isolation_scope=scope,
    )


def excluded_from_production(issue: str) -> bool:
    return issue in SANDBOX_ONLY_ISSUES


def filter_patches_by_isolation(
    patches: list[dict[str, Any]],
    signal: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (allowed_patches, blocked_patches) after isolation checks."""
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for patch in patches:
        check = check_patch_isolation(patch, signal)
        patch = dict(patch)
        patch["isolation_check"] = check.to_dict()
        if check.allowed:
            allowed.append(patch)
        else:
            patch["status"] = "isolation_blocked"
            blocked.append(patch)
    return allowed, blocked