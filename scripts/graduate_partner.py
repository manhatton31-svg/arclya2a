#!/usr/bin/env python3
"""Operator CLI: graduate a sandbox partner to production."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.partners.graduation import (
    GraduationError,
    assess_graduation_readiness,
    graduate_partner,
    resolve_partner_identifier,
)
from arclya2a.server.operator_auth import load_operator_key


def _format_blockers(assessment: dict) -> str:
    lines = [
        f"Partner:     {assessment.get('partner_id')}",
        f"Agent:       {assessment.get('agent_name', '?')}",
        f"Status:      {assessment.get('status', '?')}",
        f"Ready:       {assessment.get('graduation_ready', False)}",
        "",
        "Blocking reasons:",
    ]
    for reason in assessment.get("reasons") or ["Unknown"]:
        lines.append(f"  • {reason}")
    milestones = assessment.get("milestones") or {}
    labels = assessment.get("milestone_labels") or {}
    if milestones:
        lines.extend(["", "Milestones:"])
        for mid, complete in milestones.items():
            mark = "✓" if complete else "✗"
            lines.append(f"  {mark} {labels.get(mid, mid)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Graduate a sandbox partner to production")
    parser.add_argument("partner", nargs="?", help="partner_id (tp_...) or sandbox key")
    parser.add_argument("--partner-id", dest="partner_id", help="Explicit partner_id")
    parser.add_argument("--sandbox-key", dest="sandbox_key", help="Sandbox API key to resolve partner")
    parser.add_argument(
        "--performed-by",
        default=os.environ.get("ARCLYA_OPERATOR_ID", "operator_cli"),
        help="Operator identity for audit log",
    )
    parser.add_argument(
        "--operator-key",
        default=os.environ.get("ARCLYA_OPERATOR_KEY", "").strip() or None,
        help="Operator key (default: ARCLYA_OPERATOR_KEY env)",
    )
    parser.add_argument("--check-only", action="store_true", help="Assess readiness without graduating")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    operator_key = args.operator_key or load_operator_key()
    if not operator_key or len(operator_key) < 8:
        print(
            "ERROR: Operator key required (min 8 chars). Set ARCLYA_OPERATOR_KEY or pass --operator-key.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    partner_id = args.partner_id
    sandbox_key = args.sandbox_key
    if args.partner and not partner_id and not sandbox_key:
        if str(args.partner).startswith("arclya_sandbox_"):
            sandbox_key = args.partner
        else:
            partner_id = args.partner

    resolved = resolve_partner_identifier(ROOT, partner_id=partner_id, sandbox_key=sandbox_key)
    if not resolved:
        print("ERROR: Provide a valid partner_id or sandbox_key.", file=sys.stderr)
        raise SystemExit(2)

    assessment = assess_graduation_readiness(ROOT, resolved)
    if args.check_only:
        if args.json:
            print(json.dumps(assessment, indent=2, default=str))
        else:
            print(_format_blockers(assessment))
        raise SystemExit(0 if assessment.get("ready") else 1)

    if not assessment.get("ready"):
        if args.json:
            print(json.dumps({"success": False, "assessment": assessment}, indent=2, default=str))
        else:
            print("Graduation blocked:\n")
            print(_format_blockers(assessment))
        raise SystemExit(1)

    try:
        result = graduate_partner(
            ROOT,
            partner_id=resolved,
            graduated_by=args.performed_by,
        )
    except GraduationError as exc:
        if args.json:
            print(json.dumps({"success": False, "error": str(exc), "reasons": exc.reasons}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
            for reason in exc.reasons:
                print(f"  • {reason}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("=" * 72)
        print("Partner Graduated to Production")
        print("=" * 72)
        print(f"  Partner ID:          {result['partner_id']}")
        print(f"  Agent name:          {result['agent_name']}")
        print(f"  Graduated by:        {result['graduated_by']}")
        print(f"  Graduated at:        {result['graduated_at']}")
        print(f"  Production key:      {result['production_key']}")
        print(f"  Sandbox revoked:     {len(result['sandbox_keys_revoked'])} key(s)")
        for prefix in result["sandbox_keys_revoked"]:
            print(f"    - {prefix}")
        print(f"  Audit ID:            {result['audit_id']}")
        notify = result.get("notification") or {}
        if notify.get("webhook_sent"):
            print("  Notification:        webhook sent")
        else:
            print("  Notification:        logged locally")
        print("=" * 72)
        print("Store the production key securely. Sandbox keys are now invalid.")

    raise SystemExit(0)


if __name__ == "__main__":
    main()