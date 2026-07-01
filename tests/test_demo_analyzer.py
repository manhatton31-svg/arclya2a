from arclya2a.learning.demo_analyzer import analyze_demo_report, emit_demo_learning_signal


def _success_report():
    return {
        "success": True,
        "executive_summary": {
            "onboarding_complete": True,
            "deal_closed": True,
            "lead_routing_confirmed": True,
            "cta_url": "https://demo.example/signup?ref=demo",
            "all_guardrails_passed": True,
        },
        "phases": [
            {"name": "onboarding", "onboarding_complete": True, "guardrails_ok": True, "chain_matches_expected": True},
            {
                "name": "recruiter",
                "acquisition_stage": "qualified",
                "skipped_onboarding": True,
                "guardrails_ok": True,
                "chain_matches_expected": True,
            },
            {
                "name": "closer",
                "deal_closed": True,
                "lead_routing_confirmed": True,
                "cta_url": "https://demo.example/signup?ref=demo",
                "guardrails_ok": True,
                "chain_matches_expected": True,
            },
        ],
        "guardrails": {"phases_verified": True, "per_phase": []},
        "outcome": {"success": True, "lead_routing_confirmed": True},
    }


def test_analyze_demo_report_success():
    signal = analyze_demo_report(_success_report())
    assert signal["demo_success"] is True
    assert signal["meta_optimizer_target"]
    assert signal["recommendations"]


def test_analyze_demo_report_closer_failure():
    report = _success_report()
    report["phases"][2]["deal_closed"] = False
    report["phases"][2]["lead_routing_confirmed"] = False
    report["executive_summary"]["deal_closed"] = False
    signal = analyze_demo_report(report)
    assert "closer_no_commitment" in signal["issues_detected"]
    assert signal["priority"] == "high"
    assert "prompts/closer_prompt.md" in signal["prompt_targets"]


def test_emit_demo_learning_signal(root):
    signal = emit_demo_learning_signal(root, _success_report())
    assert signal["source"] == "demo_outcomes"
    path = root / "learning" / "demo_outcomes.jsonl"
    assert path.exists()