"""Operational dashboard aggregation for CLI and API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arclya2a.agents.audit import build_agent_audit_summary
from arclya2a.agents.moderation import build_agent_management_summary
from arclya2a.learning.patch_outcomes import build_dashboard as build_patch_dashboard
from arclya2a.observability.ops_events import list_ops_events
from arclya2a.observability.ops_status import build_ops_status
from arclya2a.observability.security_events import build_security_metrics
from arclya2a.partners.progress import build_partner_funnel_metrics


def build_ops_dashboard(root: Path) -> dict[str, Any]:
    """Combine ops status with learning/patch dashboard data."""
    ops = build_ops_status(root)
    patches = build_patch_dashboard(root)
    security = build_security_metrics(root)
    partners = build_partner_funnel_metrics(root)
    from arclya2a.agents.email_verification import operator_verification_outbox_summary
    from arclya2a.agents.feedback import build_feedback_ops_summary

    agent_audit = build_agent_audit_summary(root)
    agent_management = build_agent_management_summary(root)
    agent_audit["management"] = agent_management
    learning_events = list_ops_events(root, category="learning", limit=15)
    tool_events = list_ops_events(root, category="tools", limit=10)
    handoff_events = list_ops_events(root, category="handoff", limit=10)
    server_events = list_ops_events(root, category="server", limit=5)

    return {
        "status": ops.get("status"),
        "checked_at": ops.get("checked_at"),
        "system": {
            "overall_status": ops.get("status"),
            "billing_deals": ops.get("billing", {}).get("deal_count", 0),
            "scheduler_enabled": ops.get("learning", {}).get("scheduler_enabled"),
            "last_learning_run": ops.get("learning", {}).get("last_run_at"),
        },
        "learning": {
            **ops.get("learning", {}),
            "recent_runs": patches.get("recent_learning_runs", []),
            "issue_summary": patches.get("issue_summary", {}),
            "recent_events": learning_events,
        },
        "tools": {
            **ops.get("tools", {}),
            "recent_events": tool_events,
        },
        "handoffs": {
            **ops.get("handoffs", {}),
            "recent_events": handoff_events,
        },
        "patches": {
            "pending_count": patches.get("pending_count", 0),
            "pending_by_risk": patches.get("pending_by_risk", {}),
            "pending_high_risk": ops.get("pending_high_risk_patches", []),
            "pending_high_risk_count": ops.get("pending_high_risk_count", 0),
            "outcome_stats": patches.get("outcome_stats", {}),
            "security_outcomes": patches.get("security_outcomes", {}),
            "recent_applied": patches.get("recent_applied", []),
        },
        "security": security,
        "partners": partners,
        "agents": agent_audit,
        "agent_feedback": build_feedback_ops_summary(root),
        "email_verification": operator_verification_outbox_summary(root, limit=10),
        "payments": ops.get("payments", {}),
        "server_events": server_events,
    }


def format_ops_dashboard_text(dashboard: dict[str, Any]) -> str:
    """Render dashboard as human-readable CLI output."""
    lines = [
        "=" * 72,
        "Arclya Operational Dashboard",
        "=" * 72,
        f"  System status:       {dashboard.get('status', 'unknown')}",
        f"  Billing deals:       {dashboard.get('system', {}).get('billing_deals', 0)}",
        f"  Scheduler enabled:   {dashboard.get('system', {}).get('scheduler_enabled', False)}",
        f"  Last learning run:   {dashboard.get('system', {}).get('last_learning_run', 'never')}",
    ]

    learning = dashboard.get("learning", {})
    issue_summary = learning.get("issue_summary", {})
    lines.extend([
        "",
        "── Learning ──",
        f"  Issues improved:     {issue_summary.get('improved_count', 0)}",
        f"  Issues still open:   {issue_summary.get('still_open_count', 0)}",
    ])
    for run in learning.get("recent_runs", [])[:5]:
        lines.append(
            f"  [{run.get('trigger', '?'):10}] patches={run.get('patches_created', 0)}/"
            f"{run.get('patches_applied', 0)} @ {str(run.get('timestamp', ''))[:19]}"
        )

    tools = dashboard.get("tools", {})
    summary = tools.get("summary", {})
    lines.extend([
        "",
        "── Tool Health ──",
        f"  Recent executions:   {summary.get('total', 0)}",
        f"  Failed:              {summary.get('failed', 0)}",
        f"  Failure rate:        {tools.get('failure_rate', 0):.1%}",
        f"  Avg duration (ms):   {summary.get('avg_duration_ms', 0)}",
    ])

    handoffs = dashboard.get("handoffs", {})
    lines.extend([
        "",
        "── Handoffs ──",
        f"  Requests:            {handoffs.get('requests', 0)}",
        f"  Completed:           {handoffs.get('completed', 0)}",
        f"  Emergency stops:     {handoffs.get('emergency_stops', 0)}",
        f"  Success rate:        {handoffs.get('success_rate', 'n/a')}",
    ])

    patches = dashboard.get("patches", {})
    lines.extend([
        "",
        "── Patches ──",
        f"  Pending total:       {patches.get('pending_count', 0)}",
        f"  Pending high-risk:   {patches.get('pending_high_risk_count', 0)}",
    ])
    for p in patches.get("pending_high_risk", [])[:5]:
        lines.append(f"    ! {p.get('issue', '?')}: {p.get('weakness', '')[:50]}")

    sec = dashboard.get("security", {})
    c24 = sec.get("counts_24h", {})
    lines.extend([
        "",
        "── Security ──",
        f"  Incidents (24h):       {c24.get('total', 0)}",
        f"  Injection rejections:  {c24.get('injection_scan_rejection', 0)}",
        f"  Tool gate blocks:      {c24.get('tool_gate_block', 0)}",
        f"  Isolation-blocked:     {sec.get('isolation_blocked_patches', 0)}",
    ])
    for e in sec.get("recent_incidents", [])[:3]:
        lines.append(
            f"    [{e.get('severity', '?'):6}] {e.get('event_type', '?')} "
            f"{e.get('reason_code') or '-'} partner={e.get('partner_id') or '-'}"
        )

    security_learning = patches.get("security_outcomes", {})
    if security_learning:
        lines.extend([
            "",
            "── Security Learning ──",
            f"  Tracked patches:     {security_learning.get('tracked_security_patches', 0)}",
            f"  Patch success rate:  {security_learning.get('success_rate', 'n/a')}",
            f"  Latest incidents:    {security_learning.get('latest_incident_total', 0)}",
        ])

    payments = dashboard.get("payments", {})
    if payments:
        by_status = payments.get("by_status") or {}
        lines.extend([
            "",
            "── Crypto Payments ──",
            f"  Enabled:             {payments.get('enabled', False)}",
            f"  Configured:          {payments.get('configured', False)}",
            f"  Token:               {payments.get('token', '?')}",
            f"  Networks:            {', '.join(payments.get('networks') or []) or 'none'}",
            f"  Payments recorded:   {payments.get('payment_count', 0)}",
            f"  Payment intents:     {payments.get('intent_count', 0)}",
            f"  Pending:             {by_status.get('pending', 0)}",
            f"  Submitted:           {by_status.get('submitted', 0)}",
            f"  Confirmed:           {by_status.get('confirmed', 0)}",
            f"  Failed:              {by_status.get('failed', 0)}",
            f"  Needs review:        {payments.get('pending_review_count', 0)}",
            f"  Confirmed (USD):     {payments.get('confirmed_total_usd', 0)}",
        ])
        for net in payments.get("networks") or []:
            masked = (payments.get("wallets_masked") or {}).get(net, "?")
            lines.append(f"    {net:10} {masked}")
        for p in payments.get("pending_review") or [][:5]:
            lines.append(
                f"    ! {p.get('payment_id', '?'):16} "
                f"{p.get('status', '?'):10} "
                f"${p.get('amount', 0)} {p.get('network', '?')} "
                f"tx={p.get('tx_hash') or 'none'}"
            )

    agents = dashboard.get("agents", {})
    if agents:
        counts = agents.get("counts_24h", {})
        mgmt = agents.get("management", {})
        lines.extend([
            "",
            "── External Agents ──",
            f"  Total agents:          {mgmt.get('total_agents', 0)}",
            f"  Active:                {mgmt.get('active', 0)}",
            f"  Suspended:             {mgmt.get('suspended', 0)}",
            f"  Pending review:        {mgmt.get('pending_review', 0)}",
            f"  Publicly listed:       {mgmt.get('publicly_listed', 0)}",
            f"  Registered (7d):       {mgmt.get('registered_last_7d', 0)}",
            f"  Suspended (7d):        {mgmt.get('suspended_last_7d', 0)}",
            f"  Audit events (total):  {agents.get('total_events', 0)}",
            f"  Suspicious (24h):      {agents.get('suspicious_24h', 0)}",
            f"  Registrations (24h):   {counts.get('agent_registered', 0)}",
            f"  Profile updates (24h): {counts.get('agent_profile_updated', 0)}",
            f"  Directory searches:    {counts.get('agent_directory_search', 0)}",
            f"  Auth failures (24h):   {counts.get('agent_auth_failure', 0)}",
            f"  Operator manage:       GET /agents/manage",
            f"  Operator audit:        GET /agents/audit",
        ])
        for reg in mgmt.get("recently_registered", [])[:3]:
            lines.append(
                f"    + {reg.get('agent_name', '?'):20} "
                f"{reg.get('agent_id', '?'):14} "
                f"status={reg.get('status', '?')}"
            )
        for e in agents.get("recent_events", [])[:3]:
            lines.append(
                f"    [{e.get('event_type', '?'):28}] "
                f"agent={e.get('agent_id') or '-':14} "
                f"suspicious={bool(e.get('suspicious'))}"
            )

    email_verify = dashboard.get("email_verification", {})
    if email_verify:
        stats = email_verify.get("delivery_stats") or {}
        lines.extend([
            "",
            "── Email Verification ──",
            f"  Delivery mode:         {email_verify.get('delivery_mode_effective', '?')}",
            f"  Pending verifications: {email_verify.get('pending_count', 0)}",
            f"  Outbox (smtp sent):    {stats.get('smtp', 0)} / outbox={stats.get('outbox', 0)}",
            f"  SMTP failures logged:  {stats.get('smtp_failed', 0)}",
            f"  Operator outbox:       GET /agents/operator/verification-outbox",
        ])
        for blocker in (email_verify.get("delivery_blockers") or [])[:2]:
            lines.append(f"    ! {blocker}")
        for row in (email_verify.get("pending_verifications") or [])[:3]:
            lines.append(
                f"    pending {row.get('agent_id', '?'):14} "
                f"{row.get('email', '?'):28} "
                f"delivery={row.get('latest_delivery') or '-'}"
            )

    feedback = dashboard.get("agent_feedback", {})
    if feedback:
        prefs = feedback.get("preferences") or {}
        lines.extend([
            "",
            "── Agent Feedback & Preferences ──",
            f"  Total feedback:        {feedback.get('total_feedback', 0)}",
            f"  Recent (7d):           {feedback.get('recent_7d', 0)}",
            f"  Human closing interest:{feedback.get('human_closing_interest', 0)}",
            f"  Wants human closing:   {prefs.get('wants_human_closing_count', 0)}",
            f"  By closing method:     {prefs.get('by_preferred_closing_method', {})}",
        ])
        for fb in feedback.get("recent_feedback", [])[:3]:
            lines.append(
                f"    [{fb.get('category', '?'):18}] "
                f"agent={fb.get('agent_id', '-'):14} "
                f"interest={fb.get('feature_interest') or '-'}"
            )

    partners = dashboard.get("partners", {})
    if partners:
        lines.extend([
            "",
            "── Test Partner Funnel ──",
            f"  Registrations:       {partners.get('registrations', 0)}",
            f"  Profile validated:   {partners.get('profile_validated', 0)}",
            f"  Onboarding done:    {partners.get('onboarding_complete', 0)}",
            f"  Recruitment reviewed:{partners.get('recruitment_reviewed', 0)}",
            f"  Sandbox closes:      {partners.get('sandbox_closes', 0)}",
            f"  Graduation ready:    {partners.get('graduation_ready', 0)}",
            f"  Graduated:           {partners.get('graduated', 0)}",
            f"  Active (7d):         {partners.get('active_7d', 0)}",
        ])
        recent_graduations = partners.get("recent_graduations") or []
        if recent_graduations:
            lines.extend(["", "  Recent graduations:"])
            for g in recent_graduations[:5]:
                lines.append(
                    f"    {g.get('partner_id', '?'):14} "
                    f"{g.get('agent_name', '?'):20} "
                    f"by={g.get('graduated_by', '?'):12} "
                    f"at={str(g.get('timestamp', ''))[:19]}"
                )
        for p in partners.get("recent_partners", [])[:5]:
            prog = p.get("milestone_progress", {})
            lines.append(
                f"    {p.get('partner_id', '?'):14} "
                f"{prog.get('completed', 0)}/{prog.get('total', 0)} milestones "
                f"next={p.get('next_milestone', '?')} "
                f"score={p.get('behavior_score', 100)}"
            )

    lines.append("=" * 72)
    return "\n".join(lines)