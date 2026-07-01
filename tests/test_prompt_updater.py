from arclya2a.learning.prompt_updater import (
    apply_learning_signal,
    load_learned_context,
    merge_effective_prompt,
    rollback_prompt,
    snapshot_prompt_version,
)


def test_apply_learning_signal_writes_effective_prompt(root):
    signal = {
        "campaign_id": "camp_test",
        "improvement_signal": {
            "meta_optimizer_target": "prompts/outreach_worker.md",
            "recommendations": ["Use shorter subject lines"],
            "deltas": {"open_rate": -0.05},
            "priority": "high",
        },
    }
    result = apply_learning_signal(root, signal, auto_apply=True)
    assert result["agent_id"] == "outreach_worker"
    assert result["patches_created"] >= 1
    assert (root / "prompts" / "outreach_worker_effective.md").exists()
    learned = load_learned_context(root, "outreach_worker")
    assert "shorter subject" in learned


def test_rollback_prompt(root):
    merge_effective_prompt(root, "outreach_worker", ["Rollback test recommendation"])
    version = snapshot_prompt_version(root, "outreach_worker")
    merge_effective_prompt(root, "outreach_worker", ["New recommendation overwrites"])
    restored = rollback_prompt(root, "outreach_worker", version)
    assert restored["restored_version"] == version
    text = (root / "prompts" / "outreach_worker_effective.md").read_text(encoding="utf-8")
    assert "Rollback test" in text