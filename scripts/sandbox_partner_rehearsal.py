#!/usr/bin/env python3
"""Sandbox warm-close rehearsal: full test-partner lifecycle over HTTP API.

Runs validate → onboard → recruit → close in sandbox mode, then prints a
graduation-readiness report from GET /partners/me/progress.

Usage
-----
    # Against local server (start uvicorn first):
    python scripts/sandbox_partner_rehearsal.py

    # With existing sandbox key:
    ARCLYA_SANDBOX_KEY=arclya_sandbox_... python scripts/sandbox_partner_rehearsal.py

    # Remote server:
    ARCLYA_BASE_URL=https://your-host python scripts/sandbox_partner_rehearsal.py

Exit codes: 0 when graduation_ready is true, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.partners.progress import collect_blocking_issues

REHEARSAL_PROFILE: dict[str, Any] = {
    "agent_name": "Rehearsal Seller Agent",
    "product_name": "Rehearsal Lead Router",
    "product_description": (
        "Sandbox rehearsal product for agent-to-agent warm lead routing with pay-on-close tracking."
    ),
    "target_customer": "B2B SaaS agent operators evaluating Arclya A2A",
    "typical_deal_size": "$40 per routed conversion",
    "common_objections": [
        "Unclear conversion attribution",
        "Partner lead quality concerns",
        "Pay-on-close skepticism",
    ],
    "preferred_pricing_model": "success_based",
    "accepts_crypto": False,
    "destination_link": "https://rehearsal.arclya.example/signup",
    "affiliate_code": "rehearsal_sbx",
}


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any: ...
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> Any: ...


class RehearsalError(Exception):
    """Rehearsal step failed."""

    def __init__(self, step: str, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.step = step
        self.status_code = status_code
        self.body = body


def default_base_url() -> str:
    return os.environ.get("ARCLYA_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def _auth_headers(sandbox_key: str, agent_id: str) -> dict[str, str]:
    return {
        "X-Arclya-Key": sandbox_key,
        "X-Arclya-Agent-Id": agent_id,
        "Content-Type": "application/json",
    }


def _check_response(resp: Any, *, step: str, expect_status: int = 200) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        body = {"raw": getattr(resp, "text", "")}
    if resp.status_code != expect_status:
        message = "unknown error"
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            message = err.get("message", message) if isinstance(err, dict) else str(err)
        elif isinstance(body, dict):
            message = body.get("detail", message)
        raise RehearsalError(step, message, status_code=resp.status_code, body=body)
    if not isinstance(body, dict):
        raise RehearsalError(step, "Expected JSON object response", status_code=resp.status_code, body=body)
    return body


def resolve_sandbox_key(
    client: HttpClient,
    base_url: str,
    *,
    agent_name: str | None = None,
) -> tuple[str, str, str | None]:
    """Return (sandbox_key, source, partner_id). Source is 'env' or 'registered'."""
    existing = os.environ.get("ARCLYA_SANDBOX_KEY", "").strip()
    if existing:
        return existing, "env", None

    name = agent_name or f"Rehearsal Agent {uuid.uuid4().hex[:8]}"
    resp = client.post(
        f"{base_url}/partners/sandbox/register",
        json={
            "agent_name": name,
            "agent_card_url": "https://rehearsal.example/.well-known/agent-card.json",
            "target_customer": REHEARSAL_PROFILE.get("target_customer", "Sandbox rehearsal partners"),
        },
    )
    data = _check_response(resp, step="register")
    key = data.get("sandbox_key", "")
    if not key:
        raise RehearsalError("register", "Response missing sandbox_key", body=data)
    return key, "registered", data.get("partner_id")


def format_graduation_report(report: dict[str, Any]) -> str:
    """Render graduation-readiness summary for terminal output."""
    progress = report.get("progress") or {}
    milestones = progress.get("milestones") or {}
    labels = progress.get("milestone_labels") or {}
    security = progress.get("security") or {}
    lines = [
        "=" * 72,
        "Arclya Sandbox Partner Rehearsal — Graduation Report",
        "=" * 72,
        f"  Base URL:            {report.get('base_url')}",
        f"  Partner ID:          {progress.get('partner_id', report.get('partner_id', '?'))}",
        f"  Sandbox key source:  {report.get('key_source', '?')}",
        f"  Rehearsal run at:    {report.get('started_at', '')[:19]}",
        "",
        "── Lifecycle steps ──",
    ]
    for step in report.get("steps", []):
        status = "OK" if step.get("ok") else "FAIL"
        detail = step.get("detail", "")
        lines.append(f"  [{status:4}] {step.get('name', '?'):22} {detail}")

    lines.extend(["", "── Milestones ──"])
    for mid in [
        "profile_validated",
        "onboarding_complete",
        "recruitment_reviewed",
        "close_dry_run",
        "no_emergency_stops",
        "security_score_ok",
    ]:
        mark = "✓" if milestones.get(mid) else "✗"
        lines.append(f"  {mark} {labels.get(mid, mid)}")

    prog = progress.get("milestone_progress") or {}
    lines.extend([
        "",
        "── Security ──",
        f"  Behavior score:      {security.get('behavior_score', '?')}",
        f"  Emergency stops:     {security.get('emergency_stop_count', 0)}",
        f"  Failed validations:  {security.get('failed_validation_count', 0)}",
        f"  Suspicious flags:    {', '.join(security.get('suspicious_flags') or []) or 'none'}",
        "",
        "── Graduation ──",
        f"  Milestone progress:  {prog.get('completed', 0)}/{prog.get('total', 0)} "
        f"({prog.get('percent', 0)}%)",
        f"  graduation_ready:    {progress.get('graduation_ready', False)}",
    ])

    blockers = report.get("blocking_issues") or []
    if blockers:
        lines.extend(["", "── Blocking issues ──"])
        for issue in blockers:
            lines.append(f"  • {issue}")
    else:
        lines.extend(["", "── Blocking issues ──", "  (none)"])

    next_step = progress.get("next_step") or {}
    if next_step and not progress.get("graduation_ready"):
        lines.extend([
            "",
            "── Recommended next step ──",
            f"  {next_step.get('title', '')}",
            f"  Action: {next_step.get('action', '')}",
            f"  Hint:   {next_step.get('hint', '')}",
        ])

    lines.append("=" * 72)
    return "\n".join(lines)


def run_rehearsal(
    *,
    base_url: str | None = None,
    sandbox_key: str | None = None,
    http_client: HttpClient | None = None,
    agent_id: str = "rehearsal_agent",
    quiet: bool = False,
) -> dict[str, Any]:
    """Execute full sandbox lifecycle and return graduation report."""
    base = (base_url or default_base_url()).rstrip("/")
    run_id = uuid.uuid4().hex[:10]
    deal_base = f"rehearsal_{run_id}"
    started_at = datetime.now(timezone.utc).isoformat()
    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "base_url": base,
        "started_at": started_at,
        "deal_id_prefix": deal_base,
        "steps": steps,
        "graduation_ready": False,
        "exit_code": 1,
    }

    owns_client = http_client is None
    client: HttpClient = http_client or httpx.Client(timeout=120.0)

    try:
        if sandbox_key:
            key = sandbox_key
            key_source = "argument"
            partner_id: str | None = None
        else:
            key, key_source, partner_id = resolve_sandbox_key(client, base)
        report["key_source"] = key_source
        report["partner_id"] = partner_id
        headers = _auth_headers(key, agent_id)

        # Step 1 — validate profile
        validate_resp = client.post(
            f"{base}/onboarding/validate",
            json={"product_profile": REHEARSAL_PROFILE},
            headers=headers,
        )
        validate_data = _check_response(validate_resp, step="validate")
        steps.append({
            "name": "validate_profile",
            "ok": validate_data.get("valid") is True,
            "detail": (
                "valid=true"
                if validate_data.get("valid")
                else f"{validate_data.get('fields_remaining', '?')} fields remaining"
            ),
        })
        if not validate_data.get("valid"):
            raise RehearsalError("validate", "Product profile validation failed", body=validate_data)

        # Step 2 — onboarding handoff
        onboard_resp = client.post(
            f"{base}/orchestrate/handoff-chain",
            json={
                "deal_id": f"{deal_base}_onboard",
                "customer_company": "Rehearsal Partner Co",
                "task_context": "Sandbox rehearsal: complete seller onboarding and save product profile",
                "auto_route": True,
            },
            headers=headers,
        )
        onboard_data = _check_response(onboard_resp, step="onboarding_handoff")
        onboard_summary = onboard_data.get("summary") or {}
        onboard_entry = onboard_summary.get("entry_agent") or onboard_data.get("entry_agent")
        steps.append({
            "name": "onboarding_handoff",
            "ok": bool(
                onboard_summary.get("onboarding_complete") or onboard_summary.get("profile_saved")
            ),
            "detail": (
                f"entry={onboard_entry}, "
                f"profile_saved={onboard_summary.get('profile_saved')}, "
                f"emergency_stop={onboard_summary.get('emergency_stop')}"
            ),
            "summary": onboard_summary,
        })
        if onboard_summary.get("emergency_stop"):
            raise RehearsalError("onboarding_handoff", "Emergency stop during onboarding", body=onboard_data)

        # Step 3 — recruitment handoff
        recruit_resp = client.post(
            f"{base}/orchestrate/handoff-chain",
            json={
                "deal_id": f"{deal_base}_recruit",
                "customer_company": "Rehearsal Partner Co",
                "task_context": "Sandbox rehearsal: recruit partner agent for warm lead routing",
                "auto_route": True,
                "onboarding_complete": True,
                "product_profile": REHEARSAL_PROFILE,
                "acquisition_stage": "prospect",
            },
            headers=headers,
        )
        recruit_data = _check_response(recruit_resp, step="recruitment_handoff")
        recruit_summary = recruit_data.get("summary") or {}
        recruit_chain = recruit_data.get("handoff_chain") or []
        recruit_entry = recruit_summary.get("entry_agent") or recruit_data.get("entry_agent")
        recruiter_ready = any(
            (h.get("payload") or {}).get("ready_to_send")
            or ((h.get("payload") or {}).get("recruitment_draft") or {}).get("ready_to_send")
            for h in recruit_chain
            if h.get("agent_id") == "recruiter"
        )
        steps.append({
            "name": "recruitment_handoff",
            "ok": recruit_entry == "recruiter" or recruiter_ready,
            "detail": (
                f"entry={recruit_entry}, "
                f"stage={recruit_summary.get('acquisition_stage')}, "
                f"ready_to_send={recruiter_ready}"
            ),
            "summary": recruit_summary,
        })

        # Step 4 — warm close handoff
        close_resp = client.post(
            f"{base}/orchestrate/handoff-chain",
            json={
                "deal_id": f"{deal_base}_close",
                "customer_company": "Rehearsal Partner Co",
                "task_context": "Sandbox rehearsal: secure lead routing commitment from warm partner",
                "auto_route": True,
                "onboarding_complete": True,
                "product_profile": REHEARSAL_PROFILE,
                "lead_warmth": "warm",
            },
            headers=headers,
        )
        close_data = _check_response(close_resp, step="close_handoff")
        close_summary = close_data.get("summary") or {}
        steps.append({
            "name": "close_handoff",
            "ok": close_summary.get("lead_routing_confirmed") is True,
            "detail": (
                f"entry={close_summary.get('entry_agent') or close_data.get('entry_agent')}, "
                f"lead_routing_confirmed={close_summary.get('lead_routing_confirmed')}, "
                f"cta_url={close_summary.get('cta_url') or '-'}"
            ),
            "summary": close_summary,
        })
        if not close_summary.get("lead_routing_confirmed"):
            raise RehearsalError(
                "close_handoff",
                "Sandbox close did not achieve lead_routing_confirmed",
                body=close_data,
            )

        # Step 5 — graduation progress
        progress_resp = client.get(f"{base}/partners/me/progress", headers=headers)
        progress_data = _check_response(progress_resp, step="progress")
        graduation_ready = bool(progress_data.get("graduation_ready"))
        blockers = collect_blocking_issues(progress_data)

        steps.append({
            "name": "graduation_check",
            "ok": graduation_ready,
            "detail": f"graduation_ready={graduation_ready}",
        })

        report.update({
            "progress": progress_data,
            "blocking_issues": blockers,
            "graduation_ready": graduation_ready,
            "exit_code": 0 if graduation_ready else 1,
        })
        return report

    except RehearsalError as exc:
        steps.append({
            "name": exc.step,
            "ok": False,
            "detail": str(exc),
            "status_code": exc.status_code,
            "body": exc.body,
        })
        report["error"] = str(exc)
        report["exit_code"] = 1
        return report
    finally:
        if owns_client and isinstance(client, httpx.Client):
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run full sandbox test-partner lifecycle and print graduation report",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Arclya server base URL (default: ARCLYA_BASE_URL or http://127.0.0.1:8787)",
    )
    parser.add_argument(
        "--sandbox-key",
        default=None,
        help="Sandbox API key (default: ARCLYA_SANDBOX_KEY or auto-register)",
    )
    parser.add_argument(
        "--agent-id",
        default="rehearsal_agent",
        help="X-Arclya-Agent-Id header value",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    args = parser.parse_args()

    report = run_rehearsal(
        base_url=args.base_url,
        sandbox_key=args.sandbox_key or os.environ.get("ARCLYA_SANDBOX_KEY", "").strip() or None,
        agent_id=args.agent_id,
        quiet=args.json,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_graduation_report(report))

    raise SystemExit(report.get("exit_code", 1))


if __name__ == "__main__":
    main()