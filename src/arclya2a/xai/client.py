"""xAI-only inference client with prompt caching structure."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


CACHEABLE_START = "<!-- CACHEABLE_START -->"
CACHEABLE_END = "<!-- CACHEABLE_END -->"
DYNAMIC_START = "<!-- DYNAMIC_START -->"
DYNAMIC_END = "<!-- DYNAMIC_END -->"


@dataclass
class PromptAssembly:
    cacheable_instructions: str
    dynamic_context: str
    full_prompt: str
    model: str
    agent_id: str


def select_model(tier: str, core_config: dict[str, Any]) -> str:
    """Select cheapest suitable model for tier."""
    tiers = core_config.get("xai", {}).get("model_tiers", {})
    return tiers.get(tier, core_config.get("xai", {}).get("default_model", "grok-3-mini"))


def assemble_prompt(
    prompt_path: Path,
    *,
    agent_id: str,
    model: str,
    variables: dict[str, str] | None = None,
) -> PromptAssembly:
    """Split prompt file into cacheable vs dynamic sections."""
    text = prompt_path.read_text(encoding="utf-8")
    variables = variables or {}

    cacheable = _extract_section(text, CACHEABLE_START, CACHEABLE_END)
    dynamic_template = _extract_section(text, DYNAMIC_START, DYNAMIC_END)
    dynamic = dynamic_template
    for key, value in variables.items():
        dynamic = dynamic.replace(f"{{{{{key}}}}}", value)

    full = f"{cacheable}\n\n---\n\n{dynamic}"
    return PromptAssembly(
        cacheable_instructions=cacheable.strip(),
        dynamic_context=dynamic.strip(),
        full_prompt=full.strip(),
        model=model,
        agent_id=agent_id,
    )


def _extract_section(text: str, start: str, end: str) -> str:
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


class XAIClient:
    """Exclusive xAI API client."""

    XAI_HOST = "api.x.ai"

    def __init__(self, root: Path, api_key: str | None = None):
        self.root = root
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        with open(root / "config" / "core.json", encoding="utf-8") as f:
            self.core_config = json.load(f)
        self.base_url = self.core_config["xai"]["base_url"]

    def record_cost(
        self,
        *,
        agent_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> dict[str, Any]:
        """Record per-agent cost under data/cost_tracking/."""
        menu_path = self.root / "pricing" / "pricing_menu.json"
        with open(menu_path, encoding="utf-8") as f:
            menu = json.load(f)
        costs = menu["model_costs_per_1k_tokens"].get(model, {})
        input_cost = (input_tokens / 1000) * costs.get("input", 0)
        cached_cost = (cached_input_tokens / 1000) * costs.get("cached_input", costs.get("input", 0) * 0.25)
        output_cost = (output_tokens / 1000) * costs.get("output", 0)
        total = input_cost + cached_cost + output_cost

        record = {
            "agent_id": agent_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cost_usd": round(total, 6),
        }
        cost_dir = self.root / "data" / "cost_tracking"
        cost_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone

        day_file = cost_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with open(day_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Call xAI chat completions API."""
        if not self.api_key:
            raise EnvironmentError("XAI_API_KEY is not set")

        if self.XAI_HOST not in self.base_url:
            raise ValueError(f"Only xAI APIs allowed; got base_url={self.base_url}")

        payload = {"model": model, "messages": messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        usage = data.get("usage", {})
        self.record_cost(
            agent_id=agent_id,
            model=model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cached_input_tokens=usage.get("cached_tokens", 0),
        )
        return data