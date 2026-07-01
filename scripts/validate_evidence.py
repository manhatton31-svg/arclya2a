"""Validate scratch evidence against plan verification observations."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRATCH = Path(os.environ.get("SCRATCH", r"C:\Users\cwhat\AppData\Local\Temp\grok-goal-78bc0efe05fb\implementer"))
ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "unit-tests.log",
    "agent-card-run1.json",
    "agent-card-run2.json",
    "handoff-chain.json",
    "prompt-assembly.json",
    "learning-loop.json",
    "structure.txt",
]

AGENT_CARD_FIELDS = [
    "name", "description", "url", "capabilities",
    "defaultInputModes", "defaultOutputModes", "skills",
]


def main() -> int:
    errors: list[str] = []

    for name in REQUIRED:
        if not (SCRATCH / name).exists():
            errors.append(f"Missing artifact: {name}")

    xai_log = SCRATCH / "xai-call.log"
    xai_missing = SCRATCH / "xai-env-missing.log"
    if not xai_log.exists() and not xai_missing.exists():
        errors.append("Missing xai-call.log or xai-env-missing.log")

    for i in (1, 2):
        path = SCRATCH / f"agent-card-run{i}.json"
        if path.exists():
            card = json.loads(path.read_text(encoding="utf-8"))
            for field in AGENT_CARD_FIELDS:
                if field not in card:
                    errors.append(f"agent-card-run{i} missing field: {field}")
            if not card.get("skills"):
                errors.append(f"agent-card-run{i} skills empty")

    chain_path = SCRATCH / "handoff-chain.json"
    if chain_path.exists():
        chain = json.loads(chain_path.read_text(encoding="utf-8"))
        handoffs = chain.get("handoff_chain", [])
        if not handoffs:
            errors.append("handoff_chain empty")
        terminal = handoffs[-1] if handoffs else {}
        if terminal.get("status") != "COMPLETE":
            errors.append(f"terminal status not COMPLETE: {terminal.get('status')}")
        if not terminal.get("next_action"):
            errors.append("terminal missing next_action")
        if not chain.get("final_ssot"):
            errors.append("missing final_ssot")
        for h in handoffs:
            if not h.get("memory_summary"):
                errors.append(f"missing memory_summary on {h.get('agent_id')}")
            conf = h.get("validation", {}).get("confidence")
            if conf is None or not (0 <= conf <= 100):
                errors.append(f"bad confidence on {h.get('agent_id')}")
        if not chain.get("cost_records"):
            errors.append("missing cost_records")
        if not chain.get("uses_xai_inference"):
            errors.append("uses_xai_inference false/missing")

    prompt_path = SCRATCH / "prompt-assembly.json"
    if prompt_path.exists():
        pa = json.loads(prompt_path.read_text(encoding="utf-8"))
        if not pa.get("has_cacheable_section") or not pa.get("has_dynamic_section"):
            errors.append("prompt assembly not separated")

    learn_path = SCRATCH / "learning-loop.json"
    if learn_path.exists():
        learn = json.loads(learn_path.read_text(encoding="utf-8"))
        signal = learn.get("signal", {})
        if not signal.get("deltas"):
            errors.append("learning-loop missing deltas")
        if not signal.get("recommendations"):
            errors.append("learning-loop missing recommendations")

    audit_dir = ROOT / "data" / "audit"
    if not any(audit_dir.glob("*.jsonl")):
        errors.append("no audit records under data/audit")

    cost_dir = ROOT / "data" / "cost_tracking"
    if not any(cost_dir.glob("*.jsonl")):
        errors.append("no cost records under data/cost_tracking")

    log = SCRATCH / "unit-tests.log"
    if log.exists():
        text = log.read_bytes().decode("utf-8", errors="replace")
        if "FAILED" in text and "0 failed" not in text.lower():
            errors.append("unit-tests.log contains failures")
        if "passed" not in text.lower():
            errors.append("unit-tests.log missing pass summary")

    report = {
        "scratch": str(SCRATCH),
        "errors": errors,
        "passed": len(errors) == 0,
        "artifacts": sorted(p.name for p in SCRATCH.iterdir() if p.is_file()),
    }
    out = SCRATCH / "verification-report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if errors:
        print("VERIFICATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("VERIFICATION PASSED")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())