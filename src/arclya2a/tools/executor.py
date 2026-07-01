"""Execute agent tool requests via connectors with retry and observability."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from arclya2a.connectors import CONNECTORS
from arclya2a.connectors.base import ConnectorResult, dry_run_enabled
from arclya2a.partners.sandbox import (
    is_sandbox_active,
    log_sandbox_audit,
    record_sandbox_security_event,
)
from arclya2a.tools.gating import evaluate_tool_gate, log_gate_decision
from arclya2a.tools.errors import (
    AGENT_NOT_ALLOWED,
    CONNECTOR_UNAVAILABLE,
    INVALID_PARAMETERS,
    MISSING_CONNECTOR,
    UNKNOWN_TOOL,
    outcome_label,
    structured_error,
)
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.structured_log import log_event
from arclya2a.tools.observability import record_tool_execution, sanitize_params
from arclya2a.tools.registry import ToolRegistry

logger = logging.getLogger("arclya2a.tools.executor")


def _max_retries() -> int:
    raw = os.environ.get("ARCLYA_TOOL_MAX_RETRIES", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _retry_base_seconds() -> float:
    raw = os.environ.get("ARCLYA_TOOL_RETRY_BASE_MS", "500").strip()
    try:
        return max(0.05, int(raw) / 1000.0)
    except ValueError:
        return 0.5


def _build_result_row(
    *,
    tool_id: str,
    reason: str,
    connector_result: ConnectorResult | None = None,
    error_code: str | None = None,
    error: str | None = None,
    skipped: bool = False,
    transient: bool = False,
    duration_ms: float = 0.0,
    attempts: int = 1,
    input_summary: dict[str, Any] | None = None,
    audit_id: str | None = None,
) -> dict[str, Any]:
    if connector_result:
        row = connector_result.to_dict()
        row.setdefault("error_code", connector_result.error_code)
        row.setdefault("transient", connector_result.transient)
    else:
        row = {
            "tool_id": tool_id,
            "success": False,
            "skipped": skipped,
            "error_code": error_code,
            "error": error,
            "transient": transient,
        }

    row["reason"] = reason
    row["duration_ms"] = round(duration_ms, 2)
    row["attempts"] = attempts
    row["outcome"] = outcome_label(
        success=row.get("success", False),
        skipped=row.get("skipped", False),
        dry_run=row.get("dry_run", False),
    )
    if input_summary is not None:
        row["input_summary"] = input_summary
    if audit_id:
        row["audit_id"] = audit_id
    return row


def _execute_connector_with_retry(
    connector,
    *,
    tool_id: str,
    action: str,
    params: dict[str, Any],
    tool_def: dict[str, Any],
) -> tuple[ConnectorResult, int, float]:
    """Run connector.execute with exponential backoff on transient failures."""
    max_attempts = _max_retries()
    base_delay = _retry_base_seconds()
    total_ms = 0.0
    last: ConnectorResult | None = None

    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        last = connector.execute(
            tool_id=tool_id,
            action=action,
            params=params,
            tool_def=tool_def,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        total_ms += elapsed_ms

        if last.success or last.skipped or not last.transient:
            return last, attempt, total_ms

        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            time.sleep(delay)

    assert last is not None
    return last, max_attempts, total_ms


def execute_tool_requests(
    root,
    agent_id: str,
    tool_requests: list[dict[str, Any]] | None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run tool_requests from an agent LLM response; returns structured result dicts."""
    if not tool_requests:
        return []

    registry = ToolRegistry(root)
    handoff_id = (context or {}).get("handoff_id")
    results: list[dict[str, Any]] = []

    for req in tool_requests:
        tool_id = req.get("tool_id", "").strip()
        params = req.get("parameters") or req.get("params") or {}
        reason = req.get("reason", "")
        enriched = _enrich_params(params, context)
        input_summary = sanitize_params(enriched)

        gate = evaluate_tool_gate(
            root,
            agent_id=agent_id,
            tool_id=tool_id,
            request=req,
            context=context,
        )
        gate_audit = log_gate_decision(
            root,
            agent_id=agent_id,
            tool_id=tool_id,
            result=gate,
            context=context,
        )

        if not gate.allowed:
            if gate.blocked_reason_code == "SANDBOX_HIGH_RISK_TOOL":
                partner_id = (context or {}).get("partner_id")
                record_sandbox_security_event(
                    root,
                    "blocked_tool",
                    partner_id=partner_id,
                    details={"tool_id": tool_id, "agent_id": agent_id},
                )
                log_sandbox_audit(
                    root,
                    action="sandbox_tool_denied",
                    reasoning=f"Blocked high-risk tool in sandbox: {tool_id}",
                    partner_id=partner_id,
                    metadata={"tool_id": tool_id, "agent_id": agent_id},
                )
            row = _build_result_row(
                tool_id=tool_id,
                reason=reason,
                error_code=gate.blocked_reason_code or "TOOL_GATE_BLOCKED",
                error=gate.reason,
                skipped=True,
                input_summary=input_summary,
            )
            row["gate"] = gate.to_dict()
            row["gate_audit_id"] = gate_audit["id"]
            log = record_tool_execution(
                root,
                agent_id=agent_id,
                tool_id=tool_id,
                connector=None,
                action=None,
                params=enriched,
                result=row,
                reason=reason,
                handoff_id=handoff_id,
            )
            row["audit_id"] = log["audit_id"]
            results.append(row)
            continue

        tool, err = registry.validate_request(agent_id, tool_id)
        if err:
            error_code = UNKNOWN_TOOL if "Unknown tool" in err else (
                AGENT_NOT_ALLOWED if "not allowed" in err else CONNECTOR_UNAVAILABLE
            )
            row = _build_result_row(
                tool_id=tool_id,
                reason=reason,
                error_code=error_code,
                error=err,
                skipped=True,
                input_summary=input_summary,
            )
            log = record_tool_execution(
                root,
                agent_id=agent_id,
                tool_id=tool_id,
                connector=None,
                action=None,
                params=enriched,
                result=row,
                reason=reason,
                handoff_id=handoff_id,
            )
            row["audit_id"] = log["audit_id"]
            results.append(row)
            continue

        connector_name = tool["connector"]
        connector_cls = CONNECTORS.get(connector_name)
        if not connector_cls:
            row = _build_result_row(
                tool_id=tool_id,
                reason=reason,
                error_code=MISSING_CONNECTOR,
                error=f"No connector implementation: {connector_name}",
                input_summary=input_summary,
            )
            log = record_tool_execution(
                root,
                agent_id=agent_id,
                tool_id=tool_id,
                connector=connector_name,
                action=tool.get("action"),
                params=enriched,
                result=row,
                reason=reason,
                handoff_id=handoff_id,
            )
            row["audit_id"] = log["audit_id"]
            results.append(row)
            continue

        connector = connector_cls()
        connector_result, attempts, duration_ms = _execute_connector_with_retry(
            connector,
            tool_id=tool_id,
            action=tool["action"],
            params=enriched,
            tool_def=tool,
        )

        row = _build_result_row(
            tool_id=tool_id,
            reason=reason,
            connector_result=connector_result,
            duration_ms=duration_ms,
            attempts=attempts,
            input_summary=input_summary,
        )
        log = record_tool_execution(
            root,
            agent_id=agent_id,
            tool_id=tool_id,
            connector=connector_name,
            action=tool["action"],
            params=enriched,
            result=row,
            reason=reason,
            handoff_id=handoff_id,
            duration_ms=duration_ms,
            attempts=attempts,
        )
        row["audit_id"] = log["audit_id"]
        results.append(row)

    if results:
        failed = sum(1 for r in results if r.get("outcome") == "failed")
        log_event(
            logger,
            "tool_batch_complete",
            agent_id=agent_id,
            total=len(results),
            failed=failed,
            handoff_id=handoff_id,
        )
        record_ops_event(
            root,
            "tool_batch_complete",
            category="tools",
            data={
                "agent_id": agent_id,
                "total": len(results),
                "failed": failed,
                "handoff_id": handoff_id,
            },
        )

    return results


def _enrich_params(params: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """Fill common params from SSOT/context when agent omits them."""
    if not context:
        return dict(params)

    enriched = dict(params)
    ssot = context.get("ssot") or {}
    meta = ssot.get("metadata") or {}
    profile = meta.get("product_profile") or {}

    if not enriched.get("to") and meta.get("partner_contact_email"):
        enriched["to"] = meta["partner_contact_email"]

    if not enriched.get("description") and profile.get("product_name"):
        deal_id = ssot.get("deal_id", "")
        enriched.setdefault(
            "description",
            f"Arclya A2A — {profile['product_name']} (deal {deal_id})",
        )

    return enriched


def any_tool_available(root, agent_id: str) -> bool:
    return bool(ToolRegistry(root).list_for_agent(agent_id, only_available=True))


def tools_status_label(root, agent_id: str) -> str:
    registry = ToolRegistry(root)
    available = registry.list_for_agent(agent_id, only_available=True)
    if available:
        return f"{len(available)} tool(s) ready"
    if dry_run_enabled():
        return "dry-run mode (ARCLYA_TOOL_DRY_RUN)"
    return "no credentials configured"