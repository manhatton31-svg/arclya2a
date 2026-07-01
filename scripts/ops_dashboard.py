#!/usr/bin/env python3
"""Operational dashboard: learning, tools, handoffs, security, pending patches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.observability.dashboard import build_ops_dashboard, format_ops_dashboard_text
from arclya2a.observability.ops_status import build_ops_status
from arclya2a.observability.security_events import (
    build_security_metrics,
    format_security_dashboard_text,
    list_security_events,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arclya operational dashboard")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--status", action="store_true", help="Compact status only")
    parser.add_argument("--security", action="store_true", help="Security observability view")
    parser.add_argument("--event-type", dest="event_type", default=None, help="Filter security events")
    parser.add_argument("--partner-id", dest="partner_id", default=None, help="Filter by partner_id")
    parser.add_argument("--severity", default=None, help="Filter by severity")
    parser.add_argument("--hours", type=float, default=24, help="Security event lookback hours")
    parser.add_argument("--limit", type=int, default=50, help="Max security events to list")
    args = parser.parse_args()

    if args.security:
        metrics = build_security_metrics(ROOT)
        events = list_security_events(
            ROOT,
            event_type=args.event_type,
            partner_id=args.partner_id,
            severity=args.severity,
            hours=args.hours,
            limit=max(1, min(args.limit, 200)),
        )
        data = {"metrics": metrics, "events": events}
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(format_security_dashboard_text(metrics))
            if events:
                print("\n── Filtered events ──")
                for e in events[: args.limit]:
                    print(
                        f"  {e.get('timestamp', '')[:19]} "
                        f"[{e.get('severity', '?'):8}] {e.get('event_type', '?'):28} "
                        f"reason={e.get('reason_code') or '-'} partner={e.get('partner_id') or '-'}"
                    )
        return

    if args.status:
        data = build_ops_status(ROOT)
    else:
        data = build_ops_dashboard(ROOT)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    elif args.status:
        sec = data.get("security", {})
        c24 = (sec.get("counts_24h") or {}) if isinstance(sec, dict) else {}
        print(f"status={data.get('status')} tools_failure_rate={data.get('tools', {}).get('failure_rate')}")
        print(f"learning_last_run={data.get('learning', {}).get('last_run_at')}")
        print(f"pending_high_risk={data.get('pending_high_risk_count', 0)}")
        print(f"security_incidents_24h={c24.get('total', 0)}")
    else:
        print(format_ops_dashboard_text(data))


if __name__ == "__main__":
    main()