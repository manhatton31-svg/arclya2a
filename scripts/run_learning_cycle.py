#!/usr/bin/env python3
"""Run one background learning cycle or start the periodic scheduler."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.learning.learning_scheduler import (
    BackgroundLearningScheduler,
    run_learning_cycle,
    scheduler_enabled,
    should_run_learning,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arclya background learning cycle")
    parser.add_argument("--once", action="store_true", help="Run one learning cycle now")
    parser.add_argument("--force", action="store_true", help="Run even if schedule says skip")
    parser.add_argument("--daemon", action="store_true", help="Run periodic scheduler in foreground")
    parser.add_argument("--status", action="store_true", help="Show whether a run is due")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.status:
        should, reason = should_run_learning(ROOT)
        data = {"scheduler_enabled": scheduler_enabled(), "should_run": should, "reason": reason}
        print(json.dumps(data, indent=2) if args.json else f"should_run={should} reason={reason}")
        return

    if args.daemon:
        async def _run() -> None:
            scheduler = BackgroundLearningScheduler(ROOT)
            await scheduler.start()
            if not scheduler_enabled():
                print("Set ARCLYA_LEARNING_SCHEDULER_ENABLED=1 to enable the daemon.", file=sys.stderr)
            try:
                while True:
                    await asyncio.sleep(3600)
            except KeyboardInterrupt:
                await scheduler.stop()

        asyncio.run(_run())
        return

    if args.once or args.force:
        if not args.force:
            should, reason = should_run_learning(ROOT)
            if not should:
                msg = {"skipped": True, "reason": reason}
                print(json.dumps(msg, indent=2) if args.json else f"Skipped: {reason}")
                return
        result = run_learning_cycle(ROOT, trigger="manual" if args.force else "cli")
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(
                f"Learning cycle complete: "
                f"patches={result.get('patches_created')} "
                f"applied={result.get('patches_applied')} "
                f"pending_review={result.get('pending_review')}"
            )
        return

    parser.print_help()


if __name__ == "__main__":
    main()