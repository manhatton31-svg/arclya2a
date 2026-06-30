"""Campaign learning loop: predictions vs actuals."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LearningSignal:
    campaign_id: str
    deltas: dict[str, float]
    recommendations: list[str]
    priority: str
    improvement_signal: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_deltas(predicted: dict[str, float], actual: dict[str, float]) -> dict[str, float]:
    deltas = {}
    for key in predicted:
        if key in actual:
            deltas[key] = round(actual[key] - predicted[key], 4)
    return deltas


def build_recommendations(deltas: dict[str, float]) -> list[str]:
    recs = []
    for metric, delta in deltas.items():
        if delta < -0.02:
            recs.append(f"Improve {metric}: underperformed by {abs(delta):.2%}")
        elif delta > 0.02:
            recs.append(f"Reinforce {metric}: exceeded prediction by {delta:.2%}")
    if not recs:
        recs.append("Performance within tolerance; monitor next cycle")
    return recs


def run_campaign_learning_loop(root: Path, campaign_row: dict[str, Any]) -> LearningSignal:
    """Compare prediction vs actual and emit improvement signal."""
    campaign_id = campaign_row["campaign_id"]
    predicted = campaign_row["predicted"]
    actual = campaign_row["actual"]
    deltas = compute_deltas(predicted, actual)
    recommendations = build_recommendations(deltas)
    priority = "high" if any(d < -0.05 for d in deltas.values()) else "medium"

    signal = LearningSignal(
        campaign_id=campaign_id,
        deltas=deltas,
        recommendations=recommendations,
        priority=priority,
        improvement_signal={
            "deltas": deltas,
            "recommendations": recommendations,
            "priority": priority,
            "meta_optimizer_target": "prompts/outreach_worker.md",
        },
    )

    learning_dir = root / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    out_path = learning_dir / "learning_signals.jsonl"
    entry = {**signal.to_dict(), "timestamp": datetime.now(timezone.utc).isoformat()}
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return signal