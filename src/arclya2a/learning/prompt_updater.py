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


def apply_learning_signal(
    root: Path,
    signal: dict[str, Any],
    *,
    auto_apply: bool = False,
    auto_apply_low_risk: bool = True,
) -> dict[str, Any]:
    """Generate concrete patches; auto-apply low-risk when eligible."""
    from arclya2a.learning.patch_generator import (
        apply_patch_by_id,
        auto_apply_eligible_patches,
        generate_concrete_patches,
        store_prompt_patches,
    )
    from arclya2a.security.cross_agent_isolation import filter_patches_by_isolation
    from arclya2a.learning.patch_outcomes import evaluate_patch_outcomes

    improvement = signal.get("improvement_signal", signal)
    target = improvement.get("meta_optimizer_target", "prompts/outreach_worker.md")
    agent_id = Path(target).stem
    recommendations = improvement.get("recommendations", [])

    evaluate_patch_outcomes(
        root,
        improvement.get("issues_detected") or [],
        signal=improvement,
    )

    all_patches = generate_concrete_patches(improvement)
    allowed_patches, blocked_patches = filter_patches_by_isolation(all_patches, improvement)
    patch_ids = store_prompt_patches(root, allowed_patches + blocked_patches)

    learned_path = root / "prompts" / f"{agent_id}_learned.md"
    lines = [
        "<!-- DYNAMIC_LEARNED_START -->",
        "## Learned Improvements (pending review)",
        *[f"- {r}" for r in recommendations],
        "<!-- DYNAMIC_LEARNED_END -->",
    ]
    learned_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    applied_ids: list[str] = []
    auto_applied_results: list[dict[str, Any]] = []

    if auto_apply:
        for pid in patch_ids:
            try:
                apply_patch_by_id(root, pid)
                applied_ids.append(pid)
            except FileNotFoundError:
                pass
    elif auto_apply_low_risk:
        allowed_ids = [p["patch_id"] for p in allowed_patches]
        auto_applied_results = auto_apply_eligible_patches(root, allowed_ids)
        applied_ids = [r["patch_id"] for r in auto_applied_results if r.get("applied")]

    effective_path = root / "prompts" / f"{agent_id}_effective.md"
    if applied_ids:
        merge_effective_prompt(root, agent_id, recommendations)
    elif recommendations and not effective_path.exists():
        merge_effective_prompt(root, agent_id, recommendations)

    return {
        "agent_id": agent_id,
        "target": target,
        "patches_created": len(patch_ids),
        "patch_ids": patch_ids,
        "patches_applied": len(applied_ids),
        "auto_applied": auto_applied_results,
        "auto_apply": auto_apply,
        "auto_apply_low_risk": auto_apply_low_risk,
        "learned_file": str(learned_path.relative_to(root)),
        "effective_file": str(effective_path.relative_to(root)) if effective_path.exists() else None,
        "pending_review": len(allowed_patches) - len(applied_ids),
        "isolation_blocked": len(blocked_patches),
        "isolation_blocked_ids": [p.get("patch_id") for p in blocked_patches],
    }