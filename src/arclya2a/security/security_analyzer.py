"""Collect security incidents and feed defensive signals into the learning loop."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import read_audit_records

SECURITY_SIGNALS_PATH = "learning/security_signals.jsonl"

PROMPT_TARGETS = {
    "closer": "prompts/closer_prompt.md",
    "onboarding": "prompts/onboarding_prompt.md",
    "recruiter": "prompts/recruiter_prompt.md",
}

SCAN_BLOCK_ACTIONS = frozenset({"reject", "disqualify"})

SECURITY_ISSUE_THRESHOLDS = {
    "injection_rejection_min": 2,
    "tool_gate_block_min": 2,
    "partner_block_min": 2,
    "sandbox_event_min": 3,
}


def _signals_path(root: Path) -> Path:
    return root / SECURITY_SIGNALS_PATH


def _read_jsonl(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows[-limit:]


def log_security_incident(
    root: Path,
    incident_type: str,
    *,
    agent_id: str | None = None,
    partner_id: str | None = None,
    deal_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a raw security incident for the learning system to consume."""
    from arclya2a.security.cross_agent_isolation import tag_incident

    sandbox_mode = bool((details or {}).get("sandbox_mode"))
    entry = tag_incident(
        {
            "event_type": "incident",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_type": incident_type,
            "agent_id": agent_id,
            "partner_id": partner_id,
            "deal_id": deal_id,
            "details": details or {},
        },
        partner_id=partner_id,
        sandbox_mode=sandbox_mode,
    )
    path = _signals_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    from arclya2a.observability.security_events import (
        EVENT_INJECTION_SCAN_REJECTION,
        EVENT_TOOL_GATE_BLOCK,
        record_security_event,
    )

    details = details or {}
    obs_type = details.get("observability_event_type")
    if not obs_type:
        obs_type = (
            EVENT_INJECTION_SCAN_REJECTION
            if incident_type == "injection_scan_block"
            else EVENT_TOOL_GATE_BLOCK
            if incident_type == "tool_gate_block"
            else incident_type
        )
    record_security_event(
        root,
        obs_type,
        reason_code=details.get("blocked_reason_code") or details.get("recommended_action") or incident_type,
        partner_id=partner_id,
        agent_id=agent_id,
        deal_id=deal_id,
        scan_id=details.get("scan_id"),
        handoff_id=details.get("handoff_id"),
        sandbox_mode=sandbox_mode,
        isolation_scope=entry.get("isolation_scope"),
        details=details,
    )
    return entry


def load_injection_scan_events(root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    return _read_jsonl(root / "learning" / "injection_scan_events.jsonl", limit=limit)


def load_sandbox_security_events(root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    return _read_jsonl(root / "data" / "test_partners" / "security_events.jsonl", limit=limit)


def load_tool_gate_blocks(root: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for record in read_audit_records(root, limit=limit * 3):
        if record.get("action") != "tool_gate_blocked":
            continue
        meta = record.get("metadata") or {}
        if meta.get("category") != "tool_gating":
            continue
        blocks.append({
            "timestamp": record.get("timestamp"),
            "agent_id": record.get("agent_id"),
            "tool_id": meta.get("tool_id"),
            "blocked_reason_code": meta.get("blocked_reason_code"),
            "deal_id": meta.get("deal_id"),
            "commitment_state": meta.get("commitment_state"),
            "sandbox_active": meta.get("sandbox_active"),
        })
    return blocks[-limit:]


def load_emergency_stop_events(root: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    stops: list[dict[str, Any]] = []
    for record in read_audit_records(root, limit=limit * 5):
        action = record.get("action", "")
        if action not in ("emergency_stop", "handoff_emergency_stop"):
            continue
        meta = record.get("metadata") or {}
        payload = meta.get("payload") or {}
        security_flags = meta.get("security_flags") or payload.get("security_flags") or []
        security_scan = payload.get("security_scan") or meta.get("security_scan")
        if not security_flags and not security_scan:
            continue
        stops.append({
            "timestamp": record.get("timestamp"),
            "agent_id": record.get("agent_id"),
            "next_action": meta.get("next_action") or payload.get("next_action"),
            "security_flags": security_flags,
            "security_scan": security_scan,
            "deal_id": meta.get("deal_id"),
        })
    return stops[-limit:]


def _pattern_slug(pattern_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", pattern_id.lower()).strip("_")
    return slug[:48] or "pattern"


def analyze_injection_scans(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize injection scan rejections and recurring patterns."""
    rejections = [
        e for e in events
        if e.get("recommended_action") in SCAN_BLOCK_ACTIONS or e.get("is_suspicious")
    ]
    blocks = [e for e in events if e.get("recommended_action") in SCAN_BLOCK_ACTIONS]

    pattern_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    partner_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    for event in rejections:
        agent_counts[event.get("agent_id", "unknown")] += 1
        if event.get("partner_id"):
            partner_counts[event["partner_id"]] += 1
        for pattern in event.get("detected_patterns") or []:
            pattern_counts[pattern.get("id", "unknown")] += 1
            source = pattern.get("source")
            if source:
                source_counts[source] += 1

    issues: list[str] = []
    recommendations: list[str] = []
    suggested_patterns: list[dict[str, Any]] = []

    if len(blocks) >= SECURITY_ISSUE_THRESHOLDS["injection_rejection_min"]:
        issues.append("injection_scan_rejection")
        recommendations.append(
            f"{len(blocks)} injection scan blocks — strengthen Closer disqualification and scanner patterns"
        )

    disqualifies = [e for e in blocks if e.get("recommended_action") == "disqualify"]
    if disqualifies:
        issues.append("injection_scan_disqualify")
        recommendations.append(
            f"{len(disqualifies)} closer disqualifications from injection scan — tighten disqualification triggers"
        )

    repeated = [(pid, count) for pid, count in pattern_counts.most_common() if count >= 2]
    if repeated:
        issues.append("repeated_injection_pattern")
        top_id, top_count = repeated[0]
        recommendations.append(
            f"Pattern '{top_id}' detected {top_count} times — add learned injection pattern"
        )
        for pid, count in repeated[:3]:
            sample = next(
                (
                    p for e in rejections for p in (e.get("detected_patterns") or [])
                    if p.get("id") == pid
                ),
                None,
            )
            if sample:
                suggested_patterns.append({
                    "pattern_id": f"learned_{_pattern_slug(pid)}",
                    "label": sample.get("label", pid),
                    "regex": _infer_pattern_regex(sample),
                    "severity": float(sample.get("severity", 0.75)),
                    "category": "learned",
                    "occurrences": count,
                })

    high_risk_partners = [
        pid for pid, count in partner_counts.items() if count >= SECURITY_ISSUE_THRESHOLDS["partner_block_min"]
    ]
    if high_risk_partners:
        issues.append("high_risk_partner")
        recommendations.append(
            f"Partners {', '.join(high_risk_partners[:3])} triggered repeated injection blocks"
        )

    return {
        "total_scans": len(events),
        "rejections": len(rejections),
        "blocks": len(blocks),
        "disqualifies": len(disqualifies),
        "by_pattern": dict(pattern_counts),
        "by_agent": dict(agent_counts),
        "by_partner": dict(partner_counts),
        "by_source": dict(source_counts),
        "suggested_patterns": suggested_patterns,
        "issues": issues,
        "recommendations": recommendations,
    }


def _infer_pattern_regex(sample: dict[str, Any]) -> str:
    """Build a conservative regex from a detected pattern excerpt."""
    excerpt = (sample.get("excerpt") or "").strip()
    if not excerpt:
        return rf"(?i)\b{re.escape(sample.get('id', 'pattern'))}\b"
    tokens = re.findall(r"[a-zA-Z]{4,}", excerpt)
    if len(tokens) >= 2:
        core = re.escape(" ".join(tokens[:3]))
        return rf"(?i){core}"
    if tokens:
        return rf"(?i)\b{re.escape(tokens[0])}\b"
    return rf"(?i)\b{re.escape(sample.get('id', 'pattern'))}\b"


def analyze_tool_gate_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize blocked tool calls from the centralized gate."""
    reason_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()

    for block in blocks:
        code = block.get("blocked_reason_code") or "UNKNOWN"
        reason_counts[code] += 1
        agent_counts[block.get("agent_id", "unknown")] += 1
        if block.get("tool_id"):
            tool_counts[block["tool_id"]] += 1

    issues: list[str] = []
    recommendations: list[str] = []

    if len(blocks) >= SECURITY_ISSUE_THRESHOLDS["tool_gate_block_min"]:
        issues.append("tool_gate_violation")
        recommendations.append(
            f"{len(blocks)} tool gate blocks — reinforce post-commitment tool rules in Closer"
        )

    if reason_counts.get("PARTNER_REQUEST_NOT_GATE", 0) >= 1:
        issues.append("tool_gate_partner_command")
        recommendations.append(
            "Partner-commanded tool requests blocked — clarify partner cannot authorize tools"
        )

    if reason_counts.get("COMMITMENT_NOT_CONFIRMED", 0) >= 1:
        issues.append("tool_gate_premature")
        recommendations.append(
            "Tools requested before commitment gate — strengthen hard gate checklist in Closer"
        )

    if reason_counts.get("SUSPICIOUS_PARTNER_TRUST", 0) >= 1:
        issues.append("suspicious_partner_trust_block")
        recommendations.append(
            "Tools blocked for suspicious partner trust — document trust downgrade protocol"
        )

    if reason_counts.get("SANDBOX_HIGH_RISK_TOOL", 0) >= 1:
        issues.append("sandbox_tool_block")
        recommendations.append(
            "Sandbox high-risk tool blocks observed — reinforce sandbox_mode tool_requests: [] rule"
        )

    return {
        "total_blocks": len(blocks),
        "by_reason": dict(reason_counts),
        "by_agent": dict(agent_counts),
        "by_tool": dict(tool_counts),
        "issues": issues,
        "recommendations": recommendations,
    }


def analyze_sandbox_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize suspicious sandbox partner behavior."""
    type_counts: Counter[str] = Counter()
    partner_counts: Counter[str] = Counter()

    suspicious_types = {
        "blocked_tool",
        "blocked_path",
        "rate_limit",
        "validation_failure",
        "emergency_stop",
    }

    for event in events:
        event_type = event.get("event_type", "unknown")
        type_counts[event_type] += 1
        if event.get("partner_id"):
            partner_counts[event["partner_id"]] += 1

    suspicious = sum(type_counts.get(t, 0) for t in suspicious_types)
    issues: list[str] = []
    recommendations: list[str] = []

    if suspicious >= SECURITY_ISSUE_THRESHOLDS["sandbox_event_min"]:
        issues.append("sandbox_suspicious_partner")
        recommendations.append(
            f"{suspicious} sandbox security events — tighten sandbox restrictions and partner trust"
        )

    repeat_partners = [
        pid for pid, count in partner_counts.items() if count >= SECURITY_ISSUE_THRESHOLDS["partner_block_min"]
    ]
    if repeat_partners:
        issues.append("sandbox_repeat_offender")
        recommendations.append(
            f"Repeat sandbox offenders: {', '.join(repeat_partners[:3])} — flag for graduation review"
        )

    return {
        "total_events": len(events),
        "suspicious_events": suspicious,
        "by_type": dict(type_counts),
        "by_partner": dict(partner_counts),
        "issues": issues,
        "recommendations": recommendations,
    }


def analyze_emergency_stops(stops: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    recommendations: list[str] = []
    if stops:
        issues.append("emergency_stop_security")
        recommendations.append(
            f"{len(stops)} EMERGENCY_STOP events with security flags — review guardrail chain"
        )
    return {
        "total": len(stops),
        "events": stops[-5:],
        "issues": issues,
        "recommendations": recommendations,
    }


def build_security_learning_context(root: Path) -> dict[str, Any]:
    """Aggregate security data sources for Meta Optimizer defensive patches."""
    scan_events = load_injection_scan_events(root)
    gate_blocks = load_tool_gate_blocks(root)
    sandbox_events = load_sandbox_security_events(root)
    emergency_stops = load_emergency_stop_events(root)

    injection = analyze_injection_scans(scan_events)
    tool_gate = analyze_tool_gate_blocks(gate_blocks)
    sandbox = analyze_sandbox_events(sandbox_events)
    emergency = analyze_emergency_stops(emergency_stops)

    all_issues = list(dict.fromkeys(
        injection.get("issues", [])
        + tool_gate.get("issues", [])
        + sandbox.get("issues", [])
        + emergency.get("issues", [])
    ))
    all_recs = list(dict.fromkeys(
        injection.get("recommendations", [])
        + tool_gate.get("recommendations", [])
        + sandbox.get("recommendations", [])
        + emergency.get("recommendations", [])
    ))

    prompt_targets: list[str] = []
    if any(i.startswith(("injection", "tool_gate", "suspicious", "emergency")) for i in all_issues):
        prompt_targets.append(PROMPT_TARGETS["closer"])
    if "sandbox_suspicious_partner" in all_issues or "sandbox_tool_block" in all_issues:
        prompt_targets.append(PROMPT_TARGETS["closer"])

    priority = "low"
    if any(i in all_issues for i in (
        "injection_scan_rejection",
        "injection_scan_disqualify",
        "tool_gate_partner_command",
        "emergency_stop_security",
        "high_risk_partner",
    )):
        priority = "high"
    elif all_issues:
        priority = "medium"

    incident_total = (
        injection.get("blocks", 0)
        + tool_gate.get("total_blocks", 0)
        + sandbox.get("suspicious_events", 0)
        + emergency.get("total", 0)
    )

    primary_target = prompt_targets[0] if prompt_targets else PROMPT_TARGETS["closer"]

    raw_signal = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "source": "security_data",
        "injection_scans": injection,
        "tool_gate_blocks": tool_gate,
        "sandbox_events": sandbox,
        "emergency_stops": emergency,
        "suggested_patterns": injection.get("suggested_patterns", []),
        "incident_total": incident_total,
        "issues_detected": all_issues,
        "recommendations": all_recs,
        "prompt_targets": prompt_targets or [primary_target],
        "meta_optimizer_target": primary_target,
        "weakest_phase": "security" if all_issues else "none",
        "priority": priority,
        "patch_category": "defensive",
    }

    from arclya2a.security.cross_agent_isolation import apply_learning_signal_isolation

    return apply_learning_signal_isolation(raw_signal)


def emit_security_learning_signal(root: Path) -> dict[str, Any]:
    """Build security context, evaluate patch outcomes, persist signal."""
    signal = build_security_learning_context(root)
    from arclya2a.learning.patch_outcomes import evaluate_patch_outcomes

    evaluate_patch_outcomes(root, signal.get("issues_detected", []), signal=signal)
    path = _signals_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "event_type": "analysis",
        "timestamp": signal["analyzed_at"],
        "improvement_signal": signal,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return signal


def load_latest_security_signal(root: Path) -> dict[str, Any] | None:
    path = _signals_path(root)
    if not path.exists():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event_type") == "analysis" and row.get("improvement_signal"):
            return row["improvement_signal"]
        if row.get("improvement_signal"):
            return row["improvement_signal"]
    return None


def count_recent_incidents(root: Path, *, hours: int = 168) -> dict[str, int]:
    """Count raw security incidents in the signals log for trend tracking."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts: Counter[str] = Counter()
    for row in _read_jsonl(_signals_path(root), limit=2000):
        if row.get("event_type") != "incident":
            continue
        try:
            ts = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        counts[row.get("incident_type", "unknown")] += 1
    return dict(counts)


def security_patch_outcome_stats(root: Path) -> dict[str, Any]:
    """Track whether security-related patches correlate with fewer incidents."""
    from arclya2a.learning.patch_outcomes import _load_outcome_rows

    security_issues = {
        "injection_scan_rejection",
        "injection_scan_disqualify",
        "repeated_injection_pattern",
        "tool_gate_violation",
        "tool_gate_partner_command",
        "tool_gate_premature",
        "sandbox_suspicious_partner",
        "emergency_stop_security",
        "high_risk_partner",
        "suspicious_partner_trust_block",
        "sandbox_tool_block",
    }
    rows = _load_outcome_rows(root)
    security_rows = [r for r in rows if r.get("issue") in security_issues]
    resolved = sum(1 for r in security_rows if r.get("outcome") == "resolved")
    unresolved = sum(1 for r in security_rows if r.get("outcome") == "unresolved")
    pending = sum(1 for r in security_rows if r.get("outcome") == "pending")
    decided = resolved + unresolved

    recent_incidents = count_recent_incidents(root)
    latest = load_latest_security_signal(root) or {}

    return {
        "tracked_security_patches": len(security_rows),
        "resolved": resolved,
        "unresolved": unresolved,
        "pending": pending,
        "success_rate": round(resolved / decided, 4) if decided else None,
        "recent_incidents_7d": recent_incidents,
        "latest_incident_total": latest.get("incident_total"),
        "latest_issues": latest.get("issues_detected", []),
    }