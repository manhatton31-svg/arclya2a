from arclya2a.learning.prompt_updater import apply_learning_signal, load_learned_context


def test_apply_learning_signal_writes_overlay(root):
    signal = {
        "campaign_id": "camp_test",
        "improvement_signal": {
            "meta_optimizer_target": "prompts/outreach_worker.md",
            "recommendations": ["Use shorter subject lines"],
            "deltas": {"open_rate": -0.05},
            "priority": "high",
        },
    }
    result = apply_learning_signal(root, signal)
    assert result["agent_id"] == "outreach_worker"
    assert (root / "learning" / "prompt_patches" / "outreach_worker.json").exists()
    learned = load_learned_context(root, "outreach_worker")
    assert "shorter subject" in learned