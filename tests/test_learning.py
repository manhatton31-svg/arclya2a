import json

from arclya2a.learning.campaign_loop import run_campaign_learning_loop


def test_campaign_learning_loop(root):
    with open(root / "data" / "campaign_results" / "fixtures.json", encoding="utf-8") as f:
        row = json.load(f)[0]
    signal = run_campaign_learning_loop(root, row)
    assert signal.deltas
    assert signal.recommendations
    assert signal.improvement_signal["meta_optimizer_target"]
    assert signal.priority in ("high", "medium", "low")