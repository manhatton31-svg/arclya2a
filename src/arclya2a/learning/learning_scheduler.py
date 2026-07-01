"""Background learning scheduler: periodic execution analysis + safe auto-apply."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.billing.tracker import billing_summary
from arclya2a.learning.demo_analyzer import load_latest_demo_signal
from arclya2a.learning.execution_analyzer import emit_execution_learning_signal
from arclya2a.security.security_analyzer import emit_security_learning_signal
from arclya2a.learning.patch_outcomes import (
    evaluate_patch_outcomes,
    record_learning_run,
)
from arclya2a.learning.prompt_updater import apply_learning_signal
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.structured_log import log_event
from arclya2a.settings import get_settings

logger = logging.getLogger("arclya2a.learning.scheduler")


def scheduler_enabled() -> bool:
    return get_settings().learning_scheduler_enabled


def interval_hours() -> float:
    return max(0.25, float(get_settings().learning_interval_hours))


def min_deals_trigger() -> int:
    return get_settings().learning_min_deals


def check_interval_seconds() -> int:
    return max(60, get_settings().learning_check_seconds)


def _state_path(root: Path) -> Path:
    return root / "learning" / "scheduler_state.json"


def load_scheduler_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {"last_run_at": None, "last_deal_count": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"last_run_at": None, "last_deal_count": 0}


def save_scheduler_state(root: Path, state: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_run_learning(root: Path, *, force: bool = False) -> tuple[bool, str]:
    """Return whether a learning cycle should run and the trigger reason."""
    if force:
        return True, "manual"

    state = load_scheduler_state(root)
    billing = billing_summary(root)
    deal_count = int(billing.get("deal_count", 0))
    last_deal_count = int(state.get("last_deal_count", 0))

    threshold = min_deals_trigger()
    if threshold > 0 and deal_count - last_deal_count >= threshold:
        return True, "deals"

    last_run = state.get("last_run_at")
    if not last_run:
        return True, "initial"

    try:
        last_dt = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
    except ValueError:
        return True, "initial"

    elapsed = datetime.now(timezone.utc) - last_dt
    if elapsed >= timedelta(hours=interval_hours()):
        return True, "scheduled"

    return False, "skipped"


def _merge_demo_signal(signal: dict[str, Any], root: Path) -> dict[str, Any]:
    demo_signal = load_latest_demo_signal(root)
    if not demo_signal:
        return signal
    merged = dict(signal)
    merged["issues_detected"] = list(dict.fromkeys(
        list(signal.get("issues_detected", [])) + list(demo_signal.get("issues_detected", []))
    ))
    merged["recommendations"] = list(dict.fromkeys(
        list(signal.get("recommendations", [])) + list(demo_signal.get("recommendations", []))
    ))
    return merged


def _merge_security_signal(signal: dict[str, Any], security_signal: dict[str, Any]) -> dict[str, Any]:
    """Merge defensive security analysis into the primary learning signal."""
    merged = dict(signal)
    merged["issues_detected"] = list(dict.fromkeys(
        list(signal.get("issues_detected", [])) + list(security_signal.get("issues_detected", []))
    ))
    merged["recommendations"] = list(dict.fromkeys(
        list(signal.get("recommendations", [])) + list(security_signal.get("recommendations", []))
    ))
    merged["prompt_targets"] = list(dict.fromkeys(
        list(signal.get("prompt_targets", [])) + list(security_signal.get("prompt_targets", []))
    ))
    merged["injection_scans"] = security_signal.get("injection_scans")
    merged["tool_gate_blocks"] = security_signal.get("tool_gate_blocks")
    merged["sandbox_events"] = security_signal.get("sandbox_events")
    merged["emergency_stops"] = security_signal.get("emergency_stops")
    merged["suggested_patterns"] = security_signal.get("suggested_patterns", [])
    merged["incident_total"] = (
        int(signal.get("incident_total") or 0) + int(security_signal.get("incident_total") or 0)
    )
    if security_signal.get("priority") == "high":
        merged["priority"] = "high"
    elif security_signal.get("issues_detected") and merged.get("priority") == "low":
        merged["priority"] = security_signal.get("priority", "medium")
    if security_signal.get("issues_detected"):
        merged["weakest_phase"] = security_signal.get("weakest_phase") or merged.get("weakest_phase")
        merged["meta_optimizer_target"] = (
            security_signal.get("meta_optimizer_target") or merged.get("meta_optimizer_target")
        )
        merged["patch_category"] = "merged"
    merged["source"] = "merged" if merged.get("source") else security_signal.get("source")
    return merged


def run_learning_cycle(
    root: Path,
    *,
    trigger: str = "manual",
    demo_report: dict[str, Any] | None = None,
    auto_apply_low_risk: bool = True,
) -> dict[str, Any]:
    """Analyze execution and security data, evaluate patch outcomes, generate patches."""
    signal = emit_execution_learning_signal(root, demo_report)
    security_signal = emit_security_learning_signal(root)
    signal = _merge_security_signal(signal, security_signal)
    signal = _merge_demo_signal(signal, root)

    outcome_eval = evaluate_patch_outcomes(
        root,
        signal.get("issues_detected", []),
        signal=signal,
    )

    patch_result = apply_learning_signal(
        root,
        {"improvement_signal": signal},
        auto_apply=False,
        auto_apply_low_risk=auto_apply_low_risk,
    )

    billing = billing_summary(root)
    auto_applied = [
        r for r in patch_result.get("auto_applied", []) if r.get("applied")
    ]
    run_record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "issues_detected": signal.get("issues_detected", []),
        "issues_improved": outcome_eval.get("issues_improved", []),
        "issues_still_open": outcome_eval.get("issues_still_open", []),
        "patches_created": patch_result.get("patches_created", 0),
        "patches_applied": patch_result.get("patches_applied", 0),
        "pending_review": patch_result.get("pending_review", 0),
        "auto_applied_count": len(auto_applied),
        "deal_count": billing.get("deal_count", 0),
        "tool_failure_rate": (signal.get("tool_executions") or {}).get("failure_rate"),
        "security_incident_total": signal.get("incident_total"),
        "security_issues": security_signal.get("issues_detected", []),
        "priority": signal.get("priority"),
    }
    record_learning_run(root, run_record)
    record_ops_event(
        root,
        "learning_cycle_complete",
        category="learning",
        data={
            "trigger": trigger,
            "patches_created": run_record.get("patches_created"),
            "patches_applied": run_record.get("patches_applied"),
            "auto_applied_count": run_record.get("auto_applied_count"),
            "issues_still_open": run_record.get("issues_still_open"),
        },
    )
    log_event(
        logger,
        "learning_cycle_complete",
        trigger=trigger,
        patches_created=run_record.get("patches_created"),
        patches_applied=run_record.get("patches_applied"),
        open_issues=len(run_record.get("issues_still_open", [])),
    )

    state = load_scheduler_state(root)
    state["last_run_at"] = run_record["timestamp"]
    state["last_deal_count"] = billing.get("deal_count", 0)
    save_scheduler_state(root, state)

    return {
        **run_record,
        "improvement_signal": signal,
        "patch_result": patch_result,
        "outcome_evaluation": outcome_eval,
    }


class BackgroundLearningScheduler:
    """Async background loop that triggers learning cycles on schedule."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if not scheduler_enabled():
            log_event(logger, "learning_scheduler_disabled")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="arclya-learning-scheduler")
        log_event(
            logger,
            "learning_scheduler_started",
            interval_hours=interval_hours(),
            min_deals=min_deals_trigger(),
        )
        record_ops_event(
            self.root,
            "learning_scheduler_started",
            category="learning",
            data={"interval_hours": interval_hours(), "min_deals": min_deals_trigger()},
        )

    async def stop(self) -> None:
        log_event(logger, "learning_scheduler_stopping")
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log_event(logger, "learning_scheduler_stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            should, trigger = should_run_learning(self.root)
            if should:
                log_event(logger, "learning_cycle_start", trigger=trigger)
                record_ops_event(
                    self.root,
                    "learning_cycle_start",
                    category="learning",
                    data={"trigger": trigger},
                )
                try:
                    run_learning_cycle(self.root, trigger=trigger)
                except Exception:
                    log_event(logger, "learning_cycle_failed", trigger=trigger, level=logging.ERROR)
                    record_ops_event(
                        self.root,
                        "learning_cycle_failed",
                        category="learning",
                        data={"trigger": trigger},
                    )
                    logger.exception("learning_cycle_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=check_interval_seconds())
            except TimeoutError:
                pass