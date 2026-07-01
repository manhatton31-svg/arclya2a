"""Track whether applied patches improved outcomes over time."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _outcomes_path(root: Path) -> Path:
    return root / "learning" / "patch_outcomes.jsonl"


def _runs_path(root: Path) -> Path:
    return root / "learning" / "learning_runs.jsonl"


def _issue_snapshots_path(root: Path) -> Path:
    return root / "learning" / "issue_snapshots.jsonl"


def extract_issue_metrics(signal: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map detected issues to measurable execution metrics for before/after comparison."""
    if not signal:
        return {}

    tool = signal.get("tool_executions") or {}
    billing = signal.get("billing") or {}
    negotiation = signal.get("negotiation") or {}
    metrics: dict[str, dict[str, Any]] = {}

    if "tool_high_failure_rate" in signal.get("issues_detected", []):
        metrics["tool_high_failure_rate"] = {
            "failure_rate": tool.get("failure_rate"),
            "total_executions": tool.get("total"),
        }
    if "tool_high_skip_rate" in signal.get("issues_detected", []):
        metrics["tool_high_skip_rate"] = {"skipped_rate": tool.get("skipped_rate")}
    if "tools_called_too_early" in signal.get("issues_detected", []):
        metrics["tools_called_too_early"] = {"detected_in_tools": True}
    if "tools_called_before_close" in signal.get("issues_detected", []):
        metrics["tools_called_before_close"] = {"detected_in_demo": True}
    if "demo_no_tools_on_close" in signal.get("issues_detected", []):
        metrics["demo_no_tools_on_close"] = {"detected_in_demo": True}
    if "closer_no_commitment" in signal.get("issues_detected", []):
        metrics["closer_no_commitment"] = {"detected_in_demo": True}
    if "negotiation_too_short" in signal.get("issues_detected", []):
        metrics["negotiation_too_short"] = negotiation.get("turn_stats") or {}
    if "billing_missing_attribution" in signal.get("issues_detected", []):
        metrics["billing_missing_attribution"] = {
            "deal_count": billing.get("deal_count"),
            "affiliate_codes": billing.get("affiliate_codes"),
        }
    if "billing_low_margin" in signal.get("issues_detected", []):
        metrics["billing_low_margin"] = {
            "average_margin_percent": billing.get("average_margin_percent"),
        }
    if "billing_no_deals" in signal.get("issues_detected", []):
        metrics["billing_no_deals"] = {"deal_count": billing.get("deal_count", 0)}

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
    detected = set(signal.get("issues_detected", []))
    if detected & security_issues:
        injection = signal.get("injection_scans") or {}
        tool_gate = signal.get("tool_gate_blocks") or {}
        sandbox = signal.get("sandbox_events") or {}
        for issue in detected & security_issues:
            metrics[issue] = {
                "incident_total": signal.get("incident_total"),
                "injection_blocks": injection.get("blocks"),
                "tool_gate_blocks": tool_gate.get("total_blocks"),
                "sandbox_suspicious": sandbox.get("suspicious_events"),
            }

    return metrics


def record_patch_applied(
    root: Path,
    patch: dict[str, Any],
    *,
    baseline_issues: list[str] | None = None,
    baseline_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Log patch application with baseline issues for later outcome comparison."""
    issue = patch.get("issue")
    evidence = patch.get("evidence") or {}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "patch_id": patch.get("patch_id"),
        "agent_id": patch.get("agent_id"),
        "issue": issue,
        "risk_class": patch.get("risk_class"),
        "confidence": patch.get("confidence"),
        "auto_applied": patch.get("auto_applied", False),
        "baseline_issues": baseline_issues or evidence.get("issues_detected", []),
        "baseline_metrics": baseline_metrics or (
            {issue: {"tool_failure_rate": evidence.get("tool_failure_rate")}}
            if issue and evidence.get("tool_failure_rate") is not None
            else {}
        ),
        "check_count": 0,
        "outcome": "pending",
    }
    path = _outcomes_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _load_outcome_rows(root: Path) -> list[dict[str, Any]]:
    path = _outcomes_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_outcome_rows(root: Path, rows: list[dict[str, Any]]) -> None:
    path = _outcomes_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def record_issue_snapshot(
    root: Path,
    current_issues: list[str],
    metrics: dict[str, dict[str, Any]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist per-run issue state for trend analysis."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "issues_open": current_issues,
        "issue_metrics": metrics,
    }
    path = _issue_snapshots_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def evaluate_patch_outcomes(
    root: Path,
    current_issues: list[str],
    *,
    signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark outcomes resolved/unresolved and attach current metrics per issue."""
    rows = _load_outcome_rows(root)
    if not rows and not current_issues:
        return {
            "updated": [],
            "issues_improved": [],
            "issues_still_open": [],
            "issues_newly_resolved": [],
        }

    metrics = extract_issue_metrics(signal)
    now = datetime.now(timezone.utc).isoformat()
    updated: list[dict[str, Any]] = []
    issues_newly_resolved: list[str] = []

    for row in rows:
        if row.get("outcome") not in ("pending", "unresolved"):
            continue
        issue = row.get("issue")
        if not issue:
            continue

        row["check_count"] = int(row.get("check_count", 0)) + 1
        row["last_checked_at"] = now
        if issue in metrics:
            row["current_metrics"] = metrics[issue]

        if issue not in current_issues:
            row["outcome"] = "resolved"
            row["resolved_at"] = now
            issues_newly_resolved.append(issue)
            updated.append(row)
        elif row.get("outcome") == "pending":
            row["outcome"] = "unresolved"
            row["first_seen_still_open_at"] = now
            updated.append(row)

    if updated:
        _write_outcome_rows(root, rows)

    record_issue_snapshot(root, current_issues, metrics)

    issues_improved = [
        r.get("issue") for r in rows if r.get("outcome") == "resolved" and r.get("issue")
    ]
    issues_still_open = list(dict.fromkeys(
        [r.get("issue") for r in rows if r.get("outcome") == "unresolved" and r.get("issue")]
        + [i for i in current_issues if i not in issues_newly_resolved]
    ))

    return {
        "updated": updated,
        "issues_improved": issues_improved,
        "issues_still_open": issues_still_open,
        "issues_newly_resolved": issues_newly_resolved,
        "current_issue_metrics": metrics,
    }


def patch_success_stats(root: Path) -> dict[str, Any]:
    """Aggregate patch outcome success rate."""
    rows = _load_outcome_rows(root)
    if not rows:
        return {
            "tracked": 0,
            "resolved": 0,
            "unresolved": 0,
            "pending": 0,
            "success_rate": None,
            "auto_applied_count": 0,
        }

    resolved = sum(1 for r in rows if r.get("outcome") == "resolved")
    unresolved = sum(1 for r in rows if r.get("outcome") == "unresolved")
    pending = sum(1 for r in rows if r.get("outcome") == "pending")
    decided = resolved + unresolved
    return {
        "tracked": len(rows),
        "resolved": resolved,
        "unresolved": unresolved,
        "pending": pending,
        "success_rate": round(resolved / decided, 4) if decided else None,
        "auto_applied_count": sum(1 for r in rows if r.get("auto_applied")),
    }


def issue_status_summary(root: Path) -> dict[str, Any]:
    """Summary of issues improved vs still open from outcomes and latest run."""
    stats = patch_success_stats(root)
    rows = _load_outcome_rows(root)
    improved = sorted({r.get("issue") for r in rows if r.get("outcome") == "resolved" and r.get("issue")})
    still_open = sorted({r.get("issue") for r in rows if r.get("outcome") == "unresolved" and r.get("issue")})
    pending_check = sorted({r.get("issue") for r in rows if r.get("outcome") == "pending" and r.get("issue")})

    latest_run = list_learning_runs(root, limit=1)
    latest_issues = latest_run[0].get("issues_detected", []) if latest_run else []

    return {
        "issues_improved": improved,
        "issues_still_open": still_open,
        "issues_pending_outcome": pending_check,
        "latest_run_issues": latest_issues,
        "improved_count": len(improved),
        "still_open_count": len(still_open),
    }


def record_learning_run(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append a background or manual learning cycle record."""
    row = {
        "run_id": entry.get("run_id") or f"run_{entry.get('timestamp', datetime.now(timezone.utc).isoformat())}",
        **entry,
    }
    path = _runs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def list_learning_runs(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """List recent learning runs, newest first."""
    path = _runs_path(root)
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return rows[:limit]


def build_dashboard(root: Path) -> dict[str, Any]:
    """Dashboard data: pending, recent applied, learning runs, issue summary."""
    from arclya2a.learning.learning_scheduler import load_scheduler_state, scheduler_enabled
    from arclya2a.learning.patch_generator import list_patches
    from arclya2a.observability.security_events import build_security_metrics
    from arclya2a.security.security_analyzer import security_patch_outcome_stats

    pending = list_patches(root, status="pending")
    applied = list_patches(root, status="applied")[:20]
    stats = patch_success_stats(root)
    issue_summary = issue_status_summary(root)
    recent_runs = list_learning_runs(root, limit=10)
    scheduler_state = load_scheduler_state(root)

    by_risk: dict[str, int] = {}
    for p in pending:
        risk = p.get("risk_class", "unknown")
        by_risk[risk] = by_risk.get(risk, 0) + 1

    return {
        "pending_count": len(pending),
        "pending_by_risk": by_risk,
        "pending_patches": [
            {
                "patch_id": p.get("patch_id"),
                "weakness": p.get("weakness"),
                "risk_class": p.get("risk_class"),
                "confidence": p.get("confidence"),
                "auto_apply_eligible": p.get("auto_apply_eligible"),
                "issue": p.get("issue"),
                "timestamp": p.get("timestamp"),
            }
            for p in pending[:30]
        ],
        "recent_applied": [
            {
                "patch_id": p.get("patch_id"),
                "weakness": p.get("weakness"),
                "risk_class": p.get("risk_class"),
                "confidence": p.get("confidence"),
                "applied_at": p.get("applied_at"),
                "auto_applied": p.get("auto_applied"),
            }
            for p in applied
        ],
        "outcome_stats": stats,
        "issue_summary": issue_summary,
        "recent_learning_runs": [
            {
                "run_id": r.get("run_id"),
                "timestamp": r.get("timestamp"),
                "trigger": r.get("trigger"),
                "issues_detected": r.get("issues_detected", []),
                "issues_improved": r.get("issues_improved", []),
                "issues_still_open": r.get("issues_still_open", []),
                "patches_created": r.get("patches_created", 0),
                "patches_applied": r.get("patches_applied", 0),
                "auto_applied_count": r.get("auto_applied_count", 0),
                "pending_review": r.get("pending_review", 0),
            }
            for r in recent_runs
        ],
        "scheduler": {
            "enabled": scheduler_enabled(),
            "last_run_at": scheduler_state.get("last_run_at"),
            "last_deal_count": scheduler_state.get("last_deal_count", 0),
        },
        "security_outcomes": {
            **security_patch_outcome_stats(root),
            "observability": build_security_metrics(root),
        },
    }