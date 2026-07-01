"""Structured security event stream, metrics, and query API."""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.observability.structured_log import log_event
from arclya2a.security.cross_agent_isolation import resolve_isolation_scope

logger = logging.getLogger("arclya2a.security")

SECURITY_STREAM_PATH = "data/security/security_events.jsonl"

EVENT_INJECTION_SCAN_REJECTION = "injection_scan_rejection"
EVENT_TOOL_GATE_BLOCK = "tool_gate_block"
EVENT_EMERGENCY_STOP_SECURITY = "emergency_stop_security"
EVENT_SANDBOX_SECURITY = "sandbox_security"

SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"


def _stream_path(root: Path) -> Path:
    return root / SECURITY_STREAM_PATH


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _default_severity(event_type: str, *, reason_code: str | None = None, confidence: float | None = None) -> str:
    if event_type == EVENT_EMERGENCY_STOP_SECURITY:
        return SEVERITY_CRITICAL
    if event_type == EVENT_INJECTION_SCAN_REJECTION:
        if reason_code == "disqualify" or (confidence or 0) >= 0.85:
            return SEVERITY_HIGH
        if (confidence or 0) >= 0.65:
            return SEVERITY_MEDIUM
        return SEVERITY_LOW
    if event_type == EVENT_TOOL_GATE_BLOCK:
        if reason_code in ("SUSPICIOUS_PARTNER_TRUST", "PARTNER_REQUEST_NOT_GATE"):
            return SEVERITY_HIGH
        return SEVERITY_MEDIUM
    if event_type == EVENT_SANDBOX_SECURITY:
        if reason_code in ("emergency_stop", "blocked_path"):
            return SEVERITY_HIGH
        return SEVERITY_MEDIUM
    return SEVERITY_MEDIUM


def record_security_event(
    root: Path,
    event_type: str,
    *,
    reason_code: str | None = None,
    severity: str | None = None,
    partner_id: str | None = None,
    agent_id: str | None = None,
    deal_id: str | None = None,
    scan_id: str | None = None,
    handoff_id: str | None = None,
    sandbox_mode: bool = False,
    isolation_scope: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a structured security event to the dedicated stream, audit log, and logger."""
    now = datetime.now(timezone.utc).isoformat()
    scope = isolation_scope or resolve_isolation_scope(
        partner_id=partner_id,
        sandbox_mode=sandbox_mode,
    )
    confidence = None
    if details:
        raw = details.get("confidence")
        if raw is not None:
            try:
                confidence = float(raw)
                if confidence > 1.0:
                    confidence = confidence / 100.0
            except (TypeError, ValueError):
                confidence = None

    resolved_severity = severity or _default_severity(
        event_type,
        reason_code=reason_code,
        confidence=confidence,
    )

    event = {
        "id": str(uuid.uuid4()),
        "timestamp": now,
        "event_type": event_type,
        "reason_code": reason_code,
        "severity": resolved_severity,
        "partner_id": partner_id,
        "agent_id": agent_id,
        "deal_id": deal_id,
        "scan_id": scan_id,
        "handoff_id": handoff_id,
        "sandbox_mode": sandbox_mode,
        "isolation_scope": scope,
        "details": details or {},
    }

    path = _stream_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    audit = append_audit_record(
        root,
        agent_id=agent_id or "security",
        action=f"security_{event_type}",
        reasoning=reason_code or event_type,
        handoff_id=handoff_id,
        metadata={
            "category": "security",
            "event_type": event_type,
            "reason_code": reason_code,
            "severity": resolved_severity,
            "partner_id": partner_id,
            "deal_id": deal_id,
            "scan_id": scan_id,
            "sandbox_mode": sandbox_mode,
            "isolation_scope": scope,
            "security_event_id": event["id"],
            "details": details or {},
        },
    )
    event["audit_id"] = audit["id"]

    log_event(
        logger,
        "security_event",
        event_type=event_type,
        reason_code=reason_code,
        severity=resolved_severity,
        partner_id=partner_id,
        agent_id=agent_id,
        deal_id=deal_id,
        scan_id=scan_id,
        sandbox_mode=sandbox_mode,
    )

    return event


def _load_events(root: Path, *, limit: int = 5000) -> list[dict[str, Any]]:
    path = _stream_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows[-limit:]


def list_security_events(
    root: Path,
    *,
    event_type: str | None = None,
    partner_id: str | None = None,
    severity: str | None = None,
    hours: float | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query recent security events with optional filters."""
    cutoff: datetime | None = None
    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows: list[dict[str, Any]] = []
    for event in reversed(_load_events(root)):
        if event_type and event.get("event_type") != event_type:
            continue
        if partner_id and event.get("partner_id") != partner_id:
            continue
        if severity and event.get("severity") != severity:
            continue
        if cutoff:
            ts = _parse_ts(event.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
        rows.append(event)
        if len(rows) >= limit:
            break
    return rows


def _count_in_window(root: Path, *, hours: float, event_type: str | None = None) -> int:
    return len(list_security_events(root, event_type=event_type, hours=hours, limit=10000))


def count_isolation_blocked_patches(root: Path) -> int:
    patches_dir = root / "learning" / "prompt_patches"
    if not patches_dir.exists():
        return 0
    count = 0
    for path in patches_dir.glob("*.json"):
        if path.stem in (
            "closer_prompt", "outreach_worker", "recruiter_prompt", "onboarding_prompt",
        ):
            continue
        try:
            patch = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(patch, dict) and patch.get("status") == "isolation_blocked":
            count += 1
    return count


def _suspicious_partners(root: Path) -> list[dict[str, Any]]:
    from arclya2a.partners.test_registry import list_test_partners

    flagged: list[dict[str, Any]] = []
    for partner in list_test_partners(root, limit=200):
        sec = partner.get("security") or {}
        flags = sec.get("suspicious_flags") or []
        stops = int(sec.get("emergency_stop_count", 0))
        if flags or stops > 0:
            flagged.append({
                "partner_id": partner.get("partner_id"),
                "agent_name": partner.get("agent_name"),
                "status": partner.get("status"),
                "behavior_score": sec.get("behavior_score"),
                "emergency_stop_count": stops,
                "suspicious_flags": flags,
            })
    flagged.sort(key=lambda p: (p.get("emergency_stop_count", 0), len(p.get("suspicious_flags", []))), reverse=True)
    return flagged


def security_incident_trend(root: Path, *, days: int = 7) -> list[dict[str, Any]]:
    """Daily incident counts for trend visualization."""
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for event in _load_events(root):
        ts = _parse_ts(event.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        day = ts.strftime("%Y-%m-%d")
        buckets[day][event.get("event_type", "unknown")] += 1

    trend: list[dict[str, Any]] = []
    for offset in range(days):
        day = (datetime.now(timezone.utc) - timedelta(days=days - 1 - offset)).strftime("%Y-%m-%d")
        counts = dict(buckets.get(day, {}))
        trend.append({
            "date": day,
            "total": sum(counts.values()),
            "by_type": counts,
        })
    return trend


def build_security_metrics(root: Path) -> dict[str, Any]:
    """Aggregate security observability metrics for dashboards."""
    from arclya2a.partners.progress import build_partner_funnel_metrics

    recent = list_security_events(root, hours=24, limit=20)
    partner_funnel = build_partner_funnel_metrics(root)
    return {
        "counts_24h": {
            "total": _count_in_window(root, hours=24),
            "injection_scan_rejection": _count_in_window(
                root, hours=24, event_type=EVENT_INJECTION_SCAN_REJECTION,
            ),
            "tool_gate_block": _count_in_window(
                root, hours=24, event_type=EVENT_TOOL_GATE_BLOCK,
            ),
            "emergency_stop_security": _count_in_window(
                root, hours=24, event_type=EVENT_EMERGENCY_STOP_SECURITY,
            ),
            "sandbox_security": _count_in_window(
                root, hours=24, event_type=EVENT_SANDBOX_SECURITY,
            ),
        },
        "counts_7d": {
            "total": _count_in_window(root, hours=168),
            "injection_scan_rejection": _count_in_window(
                root, hours=168, event_type=EVENT_INJECTION_SCAN_REJECTION,
            ),
            "tool_gate_block": _count_in_window(
                root, hours=168, event_type=EVENT_TOOL_GATE_BLOCK,
            ),
            "emergency_stop_security": _count_in_window(
                root, hours=168, event_type=EVENT_EMERGENCY_STOP_SECURITY,
            ),
            "sandbox_security": _count_in_window(
                root, hours=168, event_type=EVENT_SANDBOX_SECURITY,
            ),
        },
        "recent_incidents": [
            {
                "id": e.get("id"),
                "timestamp": e.get("timestamp"),
                "event_type": e.get("event_type"),
                "reason_code": e.get("reason_code"),
                "severity": e.get("severity"),
                "partner_id": e.get("partner_id"),
                "agent_id": e.get("agent_id"),
                "scan_id": e.get("scan_id"),
            }
            for e in recent
        ],
        "suspicious_partners": _suspicious_partners(root),
        "partner_funnel": partner_funnel,
        "test_partners": partner_funnel.get("recent_partners", []),
        "isolation_blocked_patches": count_isolation_blocked_patches(root),
        "trend_7d": security_incident_trend(root, days=7),
    }


def format_security_dashboard_text(metrics: dict[str, Any]) -> str:
    """Render security metrics as CLI text."""
    c24 = metrics.get("counts_24h", {})
    c7 = metrics.get("counts_7d", {})
    lines = [
        "=" * 72,
        "Arclya Security Observability",
        "=" * 72,
        "",
        "── Last 24 hours ──",
        f"  Total incidents:           {c24.get('total', 0)}",
        f"  Injection rejections:      {c24.get('injection_scan_rejection', 0)}",
        f"  Tool gate blocks:          {c24.get('tool_gate_block', 0)}",
        f"  EMERGENCY_STOP (security): {c24.get('emergency_stop_security', 0)}",
        f"  Sandbox security:          {c24.get('sandbox_security', 0)}",
        "",
        "── Last 7 days ──",
        f"  Total incidents:           {c7.get('total', 0)}",
        f"  Injection rejections:      {c7.get('injection_scan_rejection', 0)}",
        f"  Tool gate blocks:          {c7.get('tool_gate_block', 0)}",
        "",
        f"  Isolation-blocked patches: {metrics.get('isolation_blocked_patches', 0)}",
    ]
    funnel = metrics.get("partner_funnel") or {}
    if funnel:
        lines.extend([
            "",
            "── Test Partner Funnel ──",
            f"  Registrations:         {funnel.get('registrations', 0)}",
            f"  Profile validated:     {funnel.get('profile_validated', 0)}",
            f"  Onboarding done:       {funnel.get('onboarding_complete', 0)}",
            f"  Recruitment reviewed:  {funnel.get('recruitment_reviewed', 0)}",
            f"  Sandbox closes:        {funnel.get('sandbox_closes', 0)}",
            f"  Graduation ready:      {funnel.get('graduation_ready', 0)}",
            f"  Graduated:             {funnel.get('graduated', 0)}",
        ])
        recent_graduations = funnel.get("recent_graduations") or []
        if recent_graduations:
            lines.extend(["", "  Recent graduations:"])
            for g in recent_graduations[:5]:
                lines.append(
                    f"    {g.get('partner_id', '?'):14} "
                    f"{g.get('agent_name', '?'):20} "
                    f"by={g.get('graduated_by', '?'):12} "
                    f"at={str(g.get('timestamp', ''))[:19]}"
                )

    test_partners = metrics.get("test_partners") or []
    if test_partners:
        lines.extend(["", "── Test partners (milestones) ──"])
        for p in test_partners[:8]:
            prog = p.get("milestone_progress", {})
            flags = ",".join(p.get("suspicious_flags") or []) or "none"
            lines.append(
                f"  {p.get('partner_id', '?'):14} "
                f"{prog.get('completed', 0)}/{prog.get('total', 0)} "
                f"score={p.get('behavior_score', 100)} flags={flags}"
            )

    partners = metrics.get("suspicious_partners") or []
    if partners:
        lines.extend(["", "── Suspicious partners ──"])
        for p in partners[:5]:
            lines.append(
                f"  {p.get('partner_id', '?'):14} stops={p.get('emergency_stop_count', 0)} "
                f"flags={','.join(p.get('suspicious_flags') or []) or 'none'}"
            )
    recent = metrics.get("recent_incidents") or []
    if recent:
        lines.extend(["", "── Recent incidents ──"])
        for e in recent[:8]:
            lines.append(
                f"  [{e.get('severity', '?'):8}] {e.get('event_type', '?'):28} "
                f"{e.get('reason_code') or '-':28} partner={e.get('partner_id') or '-'}"
            )
    trend = metrics.get("trend_7d") or []
    if trend:
        lines.extend(["", "── 7-day trend ──"])
        for day in trend:
            lines.append(f"  {day.get('date')}: {day.get('total', 0)} incidents")
    lines.append("=" * 72)
    return "\n".join(lines)