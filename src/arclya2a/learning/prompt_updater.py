"""Consume learning signals and apply prompt improvements."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def apply_learning_signal(root: Path, signal: dict[str, Any]) -> dict[str, Any]:
    """Apply improvement signal to target prompt via learned overlay file."""
    improvement = signal.get("improvement_signal", signal)
    target = improvement.get("meta_optimizer_target", "prompts/outreach_worker.md")
    agent_id = Path(target).stem
    recommendations = improvement.get("recommendations", [])

    patches_dir = root / "learning" / "prompt_patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch_file = patches_dir / f"{agent_id}.json"

    existing: list[dict[str, Any]] = []
    if patch_file.exists():
        existing = json.loads(patch_file.read_text(encoding="utf-8"))

    patch = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "campaign_id": signal.get("campaign_id"),
        "recommendations": recommendations,
        "deltas": improvement.get("deltas", {}),
        "priority": improvement.get("priority", "medium"),
    }
    existing.append(patch)
    patch_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    learned_path = root / "prompts" / f"{agent_id}_learned.md"
    lines = [
        "<!-- DYNAMIC_LEARNED_START -->",
        "## Learned Improvements (auto-applied)",
    ]
    for rec in recommendations:
        lines.append(f"- {rec}")
    lines.append("<!-- DYNAMIC_LEARNED_END -->")
    learned_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    applied_log = root / "learning" / "applied_patches.jsonl"
    entry = {
        "timestamp": patch["timestamp"],
        "agent_id": agent_id,
        "target": target,
        "recommendations_count": len(recommendations),
        "patch_index": len(existing) - 1,
    }
    with open(applied_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return {
        "agent_id": agent_id,
        "target": target,
        "patches_applied": len(existing),
        "learned_file": str(learned_path.relative_to(root)),
    }


def load_learned_context(root: Path, agent_id: str) -> str:
    """Load learned overlay for prompt assembly."""
    learned_path = root / "prompts" / f"{agent_id}_learned.md"
    if not learned_path.exists():
        return ""
    return learned_path.read_text(encoding="utf-8")