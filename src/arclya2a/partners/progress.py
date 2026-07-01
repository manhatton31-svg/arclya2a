"""Partner journey progress, next-step guidance, and funnel metrics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.partners.test_registry import GRADUATION_CRITERIA, get_test_partner

SUCCESS_DEFINITION = (
    "Sandbox success = warm close with summary.lead_routing_confirmed: true "
    "and summary.close_type: lead_routing_commitment (tracked CTA URL)."
)

NEXT_STEP_CATALOG: dict[str, dict[str, Any]] = {
    "validate_profile": {
        "id": "validate_profile",
        "title": "Pre-validate product profile",
        "action": "POST /onboarding/validate",
        "hint": "Send product_profile with all required fields until valid: true.",
        "success_check": "valid: true and destination_cta_preview set",
    },
    "smoke_handoff": {
        "id": "smoke_handoff",
        "title": "Run sandbox handoff chain",
        "action": "POST /orchestrate/handoff-chain",
        "hint": "Use X-Arclya-Key and X-Arclya-Agent-Id headers; confirm sandbox_mode: true.",
        "success_check": "summary.emergency_stop: false",
    },
    "complete_onboarding": {
        "id": "complete_onboarding",
        "title": "Complete onboarding in handoff chain",
        "action": "POST /orchestrate/handoff-chain",
        "hint": "Run with auto_route: true until summary.profile_saved: true.",
        "success_check": "summary.profile_saved: true",
    },
    "review_recruitment": {
        "id": "review_recruitment",
        "title": "Review recruiter outreach draft",
        "action": "POST /orchestrate/handoff-chain",
        "hint": "Second request with onboarding_complete: true and acquisition_stage: prospect.",
        "success_check": "recruiter handoff ready_to_send: true",
    },
    "sandbox_close": {
        "id": "sandbox_close",
        "title": "Complete sandbox close (lead routing commitment)",
        "action": "POST /orchestrate/handoff-chain",
        "hint": "Third request with lead_warmth: warm to trigger Closer dry run.",
        "success_check": "summary.lead_routing_confirmed: true",
    },
    "resolve_security": {
        "id": "resolve_security",
        "title": "Resolve security blockers before graduation",
        "action": "GET /partners/me/progress",
        "hint": "Avoid EMERGENCY_STOP, blocked tools/paths, and validation abuse.",
        "success_check": "milestones.no_emergency_stops and milestones.security_score_ok",
    },
    "graduate": {
        "id": "graduate",
        "title": "Request production API key",
        "action": "Contact Arclya operator",
        "hint": "All milestones complete — graduation_ready: true.",
        "success_check": "graduation_ready: true",
    },
}

MILESTONE_NEXT_STEP: list[tuple[str, str]] = [
    ("profile_validated", "validate_profile"),
    ("onboarding_complete", "complete_onboarding"),
    ("recruitment_reviewed", "review_recruitment"),
    ("close_dry_run", "sandbox_close"),
    ("no_emergency_stops", "resolve_security"),
    ("security_score_ok", "resolve_security"),
]


def _milestone_totals(milestones: dict[str, bool]) -> dict[str, Any]:
    total = len(GRADUATION_CRITERIA)
    completed = sum(1 for c in GRADUATION_CRITERIA if milestones.get(c["id"]))
    percent = round((completed / total) * 100) if total else 0
    return {"completed": completed, "total": total, "percent": percent}


def collect_blocking_issues(progress: dict[str, Any]) -> list[str]:
    """Derive human-readable blockers from a partner progress snapshot."""
    issues: list[str] = []
    labels = progress.get("milestone_labels") or {}
    for mid, complete in (progress.get("milestones") or {}).items():
        if not complete:
            issues.append(f"Milestone incomplete: {labels.get(mid, mid)}")
    security = progress.get("security") or {}
    stops = int(security.get("emergency_stop_count", 0))
    if stops > 0:
        issues.append(f"Emergency stops in sandbox history: {stops}")
    for flag in security.get("suspicious_flags") or []:
        issues.append(f"Active security flag: {flag}")
    score = security.get("behavior_score")
    if score is not None and score < 70:
        issues.append(f"Behavior score below graduation threshold: {score} (need ≥ 70)")
    return issues


def recommend_next_step(
    milestones: dict[str, bool],
    *,
    graduation_ready: bool = False,
) -> dict[str, Any]:
    """Return the highest-priority incomplete step for a sandbox partner."""
    if graduation_ready:
        return dict(NEXT_STEP_CATALOG["graduate"])

    for milestone_id, step_id in MILESTONE_NEXT_STEP:
        if not milestones.get(milestone_id):
            step = dict(NEXT_STEP_CATALOG[step_id])
            step["blocked_by_milestone"] = milestone_id
            return step

    return dict(NEXT_STEP_CATALOG["graduate"])


def build_partner_progress(root: Path, partner_id: str) -> dict[str, Any] | None:
    """Full journey snapshot for one test partner."""
    row = get_test_partner(root, partner_id)
    if not row:
        return None

    milestones = row.get("milestones") or {}
    graduation_ready = bool(row.get("graduation_ready"))
    security = row.get("security") or {}

    functional = [c for c in GRADUATION_CRITERIA if c["id"] not in ("no_emergency_stops", "security_score_ok")]
    security_criteria = [c for c in GRADUATION_CRITERIA if c["id"] in ("no_emergency_stops", "security_score_ok")]

    return {
        "partner_id": row.get("partner_id"),
        "agent_name": row.get("agent_name"),
        "status": row.get("status"),
        "registered_at": row.get("registered_at"),
        "last_seen_at": row.get("last_seen_at"),
        "handoff_count": row.get("handoff_count", 0),
        "graduation_ready": graduation_ready,
        "success_definition": SUCCESS_DEFINITION,
        "milestones": milestones,
        "milestone_labels": {c["id"]: c["label"] for c in GRADUATION_CRITERIA},
        "milestone_progress": _milestone_totals(milestones),
        "functional_milestones": {
            c["id"]: {"label": c["label"], "complete": bool(milestones.get(c["id"]))}
            for c in functional
        },
        "security_milestones": {
            c["id"]: {"label": c["label"], "complete": bool(milestones.get(c["id"]))}
            for c in security_criteria
        },
        "security": {
            "behavior_score": security.get("behavior_score", 100),
            "emergency_stop_count": security.get("emergency_stop_count", 0),
            "failed_validation_count": security.get("failed_validation_count", 0),
            "blocked_action_count": security.get("blocked_action_count", 0),
            "rate_limit_hits": security.get("rate_limit_hits", 0),
            "suspicious_flags": security.get("suspicious_flags", []),
        },
        "next_step": recommend_next_step(milestones, graduation_ready=graduation_ready),
        "progress_url": "/partners/me/progress",
        "guide_url": "/partners/onboarding/guide",
    }


def _registration_counts(root: Path) -> dict[str, int]:
    path = root / "data" / "test_partners" / "registration_log.jsonl"
    if not path.exists():
        return {"total_attempts": 0, "successful": 0, "failed": 0, "successful_7d": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    total = successful = failed = successful_7d = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        total += 1
        if row.get("success"):
            successful += 1
            ts = row.get("timestamp", "")
            try:
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff:
                    successful_7d += 1
            except ValueError:
                pass
        else:
            failed += 1
    return {
        "total_attempts": total,
        "successful": successful,
        "failed": failed,
        "successful_7d": successful_7d,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def build_partner_funnel_metrics(root: Path, *, partner_limit: int = 20) -> dict[str, Any]:
    """Aggregate test-partner funnel for ops and security dashboards."""
    from arclya2a.partners.test_registry import list_test_partners

    partners = list_test_partners(root, limit=500)
    reg = _registration_counts(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    def _milestone_count(mid: str) -> int:
        return sum(1 for p in partners if (p.get("milestones") or {}).get(mid))

    active_7d = 0
    for p in partners:
        seen = p.get("last_seen_at")
        if not seen:
            continue
        try:
            if datetime.fromisoformat(seen.replace("Z", "+00:00")) >= cutoff:
                active_7d += 1
        except ValueError:
            continue

    from arclya2a.partners.graduation import list_graduation_events

    registrations = len(partners)
    validated = _milestone_count("profile_validated")
    onboarded = _milestone_count("onboarding_complete")
    recruited = _milestone_count("recruitment_reviewed")
    closed = _milestone_count("close_dry_run")
    graduation_ready_count = sum(
        1 for p in partners
        if p.get("graduation_ready") and p.get("status") != "graduated"
    )
    promoted_count = sum(1 for p in partners if p.get("status") == "graduated")
    recent_graduations = list_graduation_events(root, limit=partner_limit)
    if promoted_count < len(recent_graduations):
        promoted_count = len(recent_graduations)
    stuck_registration = sum(
        1 for p in partners
        if not any((p.get("milestones") or {}).values())
    )

    recent: list[dict[str, Any]] = []
    for p in partners[:partner_limit]:
        milestones = p.get("milestones") or {}
        recent.append({
            "partner_id": p.get("partner_id"),
            "agent_name": p.get("agent_name"),
            "status": p.get("status"),
            "registered_at": p.get("registered_at"),
            "last_seen_at": p.get("last_seen_at"),
            "handoff_count": p.get("handoff_count", 0),
            "graduation_ready": p.get("graduation_ready", False),
            "milestone_progress": _milestone_totals(milestones),
            "next_milestone": recommend_next_step(
                milestones,
                graduation_ready=bool(p.get("graduation_ready")),
            ).get("id"),
            "behavior_score": (p.get("security") or {}).get("behavior_score", 100),
            "suspicious_flags": (p.get("security") or {}).get("suspicious_flags", []),
        })

    return {
        "registrations": registrations,
        "registrations_7d": reg["successful_7d"],
        "registration_attempts": reg["total_attempts"],
        "registration_failures": reg["failed"],
        "profile_validated": validated,
        "onboarding_complete": onboarded,
        "recruitment_reviewed": recruited,
        "sandbox_closes": closed,
        "graduation_ready": graduation_ready_count,
        "graduated": promoted_count,
        "active_7d": active_7d,
        "stuck_at_registration": stuck_registration,
        "conversion_rates": {
            "registration_to_validated": _rate(validated, registrations),
            "validated_to_onboarded": _rate(onboarded, validated),
            "onboarded_to_recruited": _rate(recruited, onboarded),
            "recruited_to_close": _rate(closed, recruited),
            "close_to_graduation_ready": _rate(graduation_ready_count + promoted_count, closed),
            "graduation_ready_to_graduated": _rate(
                promoted_count,
                graduation_ready_count + promoted_count,
            ),
            "registration_to_graduated": _rate(promoted_count, registrations),
        },
        "funnel_stages": [
            {"stage": "registered", "count": registrations},
            {"stage": "profile_validated", "count": validated},
            {"stage": "onboarding_complete", "count": onboarded},
            {"stage": "recruitment_reviewed", "count": recruited},
            {"stage": "sandbox_close", "count": closed},
            {"stage": "graduation_ready", "count": graduation_ready_count},
            {"stage": "graduated", "count": promoted_count},
        ],
        "recent_partners": recent,
        "recent_graduations": recent_graduations,
    }