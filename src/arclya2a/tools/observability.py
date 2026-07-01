"""Tool execution logging and observability."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.structured_log import log_event
from arclya2a.tools.errors import outcome_label

logger = logging.getLogger("arclya2a.tools")


def _executions_dir(root: Path) -> Path:
    d = root / "data" / "tool_executions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields; keep useful debugging context."""
    safe = dict(params)
    for key in ("body", "description", "content"):
        if key in safe and isinstance(safe[key], str) and len(safe[key]) > 200:
            safe[key] = safe[key][:200] + "…"
    return safe


def record_tool_execution(
    root: Path,
    *,
    agent_id: str,
    tool_id: str,
    connector: str | None,
    action: str | None,
    params: dict[str, Any],
    result: dict[str, Any],
    reason: str = "",
    handoff_id: str | None = None,
    duration_ms: float = 0.0,
    attempts: int = 1,
) -> dict[str, Any]:
    """Persist tool execution, write audit record, and emit structured log."""
    outcome = result.get("outcome") or outcome_label(
        success=result.get("success", False),
        skipped=result.get("skipped", False),
        dry_run=result.get("dry_run", False),
    )
    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "tool_id": tool_id,
        "connector": connector,
        "action": action,
        "reason": reason,
        "handoff_id": handoff_id,
        "input_summary": sanitize_params(params),
        "outcome": outcome,
        "success": result.get("success", False),
        "skipped": result.get("skipped", False),
        "dry_run": result.get("dry_run", False),
        "error_code": result.get("error_code"),
        "error": result.get("error"),
        "duration_ms": round(duration_ms, 2),
        "attempts": attempts,
        "data": result.get("data", {}),
    }

    path = _executions_dir(root) / "tool_executions.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    audit_action = "tool_execute_success" if record["success"] else "tool_execute_failed"
    if record["skipped"]:
        audit_action = "tool_execute_skipped"

    audit = append_audit_record(
        root,
        agent_id=agent_id,
        action=audit_action,
        reasoning=reason or f"{tool_id} → {outcome}",
        handoff_id=handoff_id,
        metadata={
            "tool_id": tool_id,
            "connector": connector,
            "outcome": outcome,
            "duration_ms": record["duration_ms"],
            "attempts": attempts,
            "error_code": record.get("error_code"),
            "execution_id": record["id"],
        },
    )
    record["audit_id"] = audit["id"]

    log_event(
        logger,
        "tool_execution",
        agent_id=agent_id,
        tool_id=tool_id,
        outcome=outcome,
        duration_ms=round(duration_ms, 1),
        attempts=attempts,
        error_code=record.get("error_code"),
    )
    if outcome == "failed":
        record_ops_event(
            root,
            "tool_execution_failed",
            category="tools",
            data={
                "agent_id": agent_id,
                "tool_id": tool_id,
                "error_code": record.get("error_code"),
            },
        )

    return record


def list_tool_executions(
    root: Path,
    *,
    limit: int = 50,
    agent_id: str | None = None,
    tool_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent tool executions, newest first."""
    path = _executions_dir(root) / "tool_executions.jsonl"
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if agent_id and row.get("agent_id") != agent_id:
                continue
            if tool_id and row.get("tool_id") != tool_id:
                continue
            records.append(row)

    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]


def execution_summary(root: Path, *, limit: int = 100) -> dict[str, Any]:
    """Aggregate stats for recent tool executions."""
    rows = list_tool_executions(root, limit=limit)
    if not rows:
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0, "dry_run": 0}

    return {
        "total": len(rows),
        "success": sum(1 for r in rows if r.get("outcome") == "success"),
        "failed": sum(1 for r in rows if r.get("outcome") == "failed"),
        "skipped": sum(1 for r in rows if r.get("outcome") == "skipped"),
        "dry_run": sum(1 for r in rows if r.get("outcome") == "dry_run"),
        "avg_duration_ms": round(
            sum(r.get("duration_ms", 0) for r in rows) / len(rows),
            2,
        ),
    }