"""Consume learning signals, merge prompts with versioned rollback."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHEABLE_START = "<!-- CACHEABLE_START -->"
CACHEABLE_END = "<!-- CACHEABLE_END -->"
DYNAMIC_START = "<!-- DYNAMIC_START -->"
DYNAMIC_END = "<!-- DYNAMIC_END -->"


def resolve_prompt_path(root: Path, agent_id: str) -> Path:
    """Return effective merged prompt if present, else base."""
    effective = root / "prompts" / f"{agent_id}_effective.md"
    base = root / "prompts" / f"{agent_id}.md"
    return effective if effective.exists() else base


def load_learned_context(root: Path, agent_id: str) -> str:
    """Load learned overlay text for prompt assembly."""
    learned_path = root / "prompts" / f"{agent_id}_learned.md"
    if not learned_path.exists():
        return ""
    return learned_path.read_text(encoding="utf-8")


def _extract_section(text: str, start: str, end: str) -> str:
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def snapshot_prompt_version(root: Path, agent_id: str) -> str:
    """Snapshot current effective (or base) prompt before merge."""
    versions_dir = root / "docs" / "version-history" / "prompts"
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    source = resolve_prompt_path(root, agent_id)
    if source.exists():
        dest = versions_dir / f"{agent_id}_{version_id}.md"
        shutil.copy2(source, dest)
        return version_id
    return ""


def merge_effective_prompt(root: Path, agent_id: str, recommendations: list[str]) -> Path:
    """Merge base prompt dynamic section with learned recommendations into effective file."""
    base_path = root / "prompts" / f"{agent_id}.md"
    effective_path = root / "prompts" / f"{agent_id}_effective.md"
    base_text = base_path.read_text(encoding="utf-8")

    cacheable = _extract_section(base_text, CACHEABLE_START, CACHEABLE_END)
    dynamic = _extract_section(base_text, DYNAMIC_START, DYNAMIC_END)

    learned_block = "\n".join(["## Learned Improvements (merged)", *[f"- {r}" for r in recommendations]])
    merged_dynamic = f"{dynamic}\n\n{learned_block}".strip()

    effective_text = (
        f"{CACHEABLE_START}\n{cacheable}\n{CACHEABLE_END}\n\n"
        f"{DYNAMIC_START}\n{merged_dynamic}\n{DYNAMIC_END}\n"
    )
    effective_path.write_text(effective_text, encoding="utf-8")
    return effective_path


def rollback_prompt(root: Path, agent_id: str, version_id: str) -> dict[str, Any]:
    """Restore prompt from version-history snapshot."""
    versions_dir = root / "docs" / "version-history" / "prompts"
    snapshot = versions_dir / f"{agent_id}_{version_id}.md"
    if not snapshot.exists():
        raise FileNotFoundError(f"No snapshot {agent_id}_{version_id}")

    effective_path = root / "prompts" / f"{agent_id}_effective.md"
    shutil.copy2(snapshot, effective_path)
    return {
        "agent_id": agent_id,
        "restored_version": version_id,
        "effective_path": str(effective_path.relative_to(root)),
    }


def apply_learning_signal(root: Path, signal: dict[str, Any]) -> dict[str, Any]:
    """Apply improvement signal: patch log, learned overlay, merged effective prompt."""
    improvement = signal.get("improvement_signal", signal)
    target = improvement.get("meta_optimizer_target", "prompts/outreach_worker.md")
    agent_id = Path(target).stem
    recommendations = improvement.get("recommendations", [])

    version_id = snapshot_prompt_version(root, agent_id)

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
        "snapshot_version": version_id,
    }
    existing.append(patch)
    patch_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    learned_path = root / "prompts" / f"{agent_id}_learned.md"
    lines = [
        "<!-- DYNAMIC_LEARNED_START -->",
        "## Learned Improvements (auto-applied)",
        *[f"- {r}" for r in recommendations],
        "<!-- DYNAMIC_LEARNED_END -->",
    ]
    learned_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    effective_path = merge_effective_prompt(root, agent_id, recommendations)

    applied_log = root / "learning" / "applied_patches.jsonl"
    entry = {
        "timestamp": patch["timestamp"],
        "agent_id": agent_id,
        "target": target,
        "recommendations_count": len(recommendations),
        "patch_index": len(existing) - 1,
        "snapshot_version": version_id,
        "effective_path": str(effective_path.relative_to(root)),
    }
    with open(applied_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return {
        "agent_id": agent_id,
        "target": target,
        "patches_applied": len(existing),
        "learned_file": str(learned_path.relative_to(root)),
        "effective_file": str(effective_path.relative_to(root)),
        "snapshot_version": version_id,
    }