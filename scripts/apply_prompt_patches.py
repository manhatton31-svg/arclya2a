#!/usr/bin/env python3
"""Review, apply, and monitor prompt patches from learning/prompt_patches/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.learning.patch_generator import apply_patch_by_id, list_patches
from arclya2a.learning.patch_outcomes import build_dashboard


def _print_dashboard(dashboard: dict) -> None:
    stats = dashboard.get("outcome_stats", {})
    issue_summary = dashboard.get("issue_summary", {})
    scheduler = dashboard.get("scheduler", {})
    print("=" * 72)
    print("Arclya Prompt Patch Dashboard")
    print("=" * 72)
    print(f"  Scheduler enabled:   {scheduler.get('enabled', False)}")
    print(f"  Last learning run:   {scheduler.get('last_run_at', 'never')}")
    print(f"  Pending patches:     {dashboard.get('pending_count', 0)}")
    by_risk = dashboard.get("pending_by_risk", {})
    if by_risk:
        print(f"  Pending by risk:     {by_risk}")
    print(f"  Issues improved:     {issue_summary.get('improved_count', 0)}")
    print(f"  Issues still open:   {issue_summary.get('still_open_count', 0)}")
    if issue_summary.get("issues_still_open"):
        print(f"    open: {issue_summary['issues_still_open']}")
    print(f"  Outcome success rate: {stats.get('success_rate', 'n/a')}")
    print(f"  Tracked outcomes:    {stats.get('tracked', 0)} "
          f"(resolved={stats.get('resolved', 0)}, unresolved={stats.get('unresolved', 0)}, "
          f"pending={stats.get('pending', 0)})")
    print(f"  Auto-applied total:    {stats.get('auto_applied_count', 0)}")
    print()

    runs = dashboard.get("recent_learning_runs", [])
    if runs:
        print("── Recent Learning Runs ──")
        for r in runs[:8]:
            print(
                f"  [{r.get('trigger', '?'):10}] "
                f"patches={r.get('patches_created', 0)}/{r.get('patches_applied', 0)} applied "
                f"open={len(r.get('issues_still_open', []))}  "
                f"@ {r.get('timestamp', '')[:19]}"
            )
        print()

    pending = dashboard.get("pending_patches", [])
    if pending:
        print("── Pending (review required for medium/high risk) ──")
        for p in pending[:15]:
            eligible = "auto-ok" if p.get("auto_apply_eligible") else "review"
            print(
                f"  [{p.get('risk_class', '?'):11}] conf={p.get('confidence', 0):.2f} "
                f"{eligible:7}  {p.get('patch_id', '')[:48]}"
            )
            print(f"             {p.get('weakness', '')}")
        print()

    applied = dashboard.get("recent_applied", [])
    if applied:
        print("── Recently Applied ──")
        for p in applied[:10]:
            auto = "auto" if p.get("auto_applied") else "manual"
            print(
                f"  [{auto:6}] [{p.get('risk_class', '?'):11}] "
                f"{p.get('patch_id', '')[:48]}"
            )
            print(f"             {p.get('weakness', '')}  @ {p.get('applied_at', '')[:19]}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and apply Arclya prompt patches")
    parser.add_argument("--dashboard", action="store_true", help="Show patch dashboard")
    parser.add_argument("--list", action="store_true", help="List patches")
    parser.add_argument("--status", default="pending", help="Filter by status (pending|applied)")
    parser.add_argument("--agent", default=None, help="Filter by agent_id (e.g. closer_prompt)")
    parser.add_argument("--apply", metavar="PATCH_ID", help="Apply a specific patch by ID")
    parser.add_argument("--apply-all", action="store_true", help="Apply all pending patches")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.dashboard:
        data = build_dashboard(ROOT)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            _print_dashboard(data)
        return

    if args.list or (not args.apply and not args.apply_all):
        patches = list_patches(ROOT, status=args.status if args.list else None, agent_id=args.agent)
        if args.json:
            print(json.dumps(patches, indent=2))
        else:
            if not patches:
                print("No patches found.")
                return
            for p in patches:
                print(
                    f"{p['patch_id']}  [{p.get('status')}]  {p.get('risk_class')}  "
                    f"conf={p.get('confidence')}  {p.get('weakness')}"
                )
        return

    if args.apply:
        result = apply_patch_by_id(ROOT, args.apply)
        print(json.dumps(result, indent=2) if args.json else f"Applied: {args.apply}")
        return

    if args.apply_all:
        patches = list_patches(ROOT, status="pending", agent_id=args.agent)
        results = []
        for p in patches:
            try:
                results.append(apply_patch_by_id(ROOT, p["patch_id"]))
            except FileNotFoundError as exc:
                results.append({"patch_id": p["patch_id"], "error": str(exc)})
        print(json.dumps(results, indent=2) if args.json else f"Applied {len(results)} patch(es)")
        return


if __name__ == "__main__":
    main()