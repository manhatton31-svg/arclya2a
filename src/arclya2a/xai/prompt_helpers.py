"""Shared prompt assembly helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arclya2a.learning.prompt_updater import load_learned_context, resolve_prompt_path
from arclya2a.xai.client import PromptAssembly, assemble_prompt, select_model


def build_assembly_variables(root: Path, agent_id: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Default template variables for prompt assembly."""
    learned = load_learned_context(root, agent_id)
    base = {
        "ssot_snapshot": "{}",
        "memory_summary": "",
        "task_context": "",
        "handoff_payload": "{}",
        "pricing_snapshot": "{}",
        "content_payload": "{}",
        "campaign_results": "[]",
        "predictions": "{}",
        "learned_context": learned,
    }
    if overrides:
        base.update(overrides)
    return base


def assemble_agent_prompt(
    root: Path,
    agent_id: str,
    *,
    model_tier: str = "economy",
    variables: dict[str, str] | None = None,
) -> PromptAssembly:
    """Assemble prompt from effective (merged) or base template."""
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        import json
        core = json.load(f)
    model = select_model(model_tier, core)
    prompt_path = resolve_prompt_path(root, agent_id)
    vars_ = build_assembly_variables(root, agent_id, variables)
    return assemble_prompt(prompt_path, agent_id=agent_id, model=model, variables=vars_)


def assembly_to_response(assembly: PromptAssembly) -> dict[str, Any]:
    """Serialize PromptAssembly for API responses."""
    return {
        "agent_id": assembly.agent_id,
        "model": assembly.model,
        "cacheable_instructions": assembly.cacheable_instructions,
        "dynamic_context": assembly.dynamic_context,
        "full_prompt": assembly.full_prompt,
        "has_cacheable_section": bool(assembly.cacheable_instructions),
        "has_dynamic_section": bool(assembly.dynamic_context),
    }