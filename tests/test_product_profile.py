from arclya2a.config.product_profile import (
    build_destination_cta,
    format_validation_errors,
    validate_product_profile,
    validation_summary,
)


def _full_profile(**overrides):
    base = {
        "agent_name": "Acme Agent",
        "product_name": "Lead Router",
        "product_description": "Routes qualified leads agent-to-agent with pay-on-close tracking.",
        "target_customer": "B2B SaaS agents",
        "typical_deal_size": "$50 per closed lead",
        "common_objections": ["Price too high", "Unclear tracking", "No crypto support"],
        "preferred_pricing_model": "success_based",
        "accepts_crypto": False,
        "destination_link": "https://example.com/signup",
        "affiliate_code": "ACME99",
    }
    base.update(overrides)
    return base


def test_validate_full_profile():
    ok, missing = validate_product_profile(_full_profile())
    assert ok
    assert missing == []


def test_validate_rejects_short_description():
    ok, missing = validate_product_profile(_full_profile(product_description="Too short"))
    assert not ok
    assert any("product_description" in m for m in missing)


def test_validate_rejects_invalid_url():
    ok, missing = validate_product_profile(_full_profile(destination_link="not-a-url"))
    assert not ok
    assert any("destination_link" in m for m in missing)


def test_validate_requires_three_objections():
    ok, missing = validate_product_profile(_full_profile(common_objections=["Only one"]))
    assert not ok
    assert any("common_objections" in m for m in missing)


def test_format_validation_errors():
    errors = format_validation_errors(["destination_link(invalid_url)", "agent_name"])
    assert len(errors) == 2
    assert all("field" in e and "message" in e for e in errors)


def test_validation_summary():
    summary = validation_summary(["agent_name", "destination_link(invalid_url)"])
    assert "Fix before completing" in summary


def test_build_destination_cta_with_affiliate():
    url = build_destination_cta(_full_profile())
    assert url.startswith("https://example.com/signup")
    assert "ref=ACME99" in url


def test_onboarding_saves_valid_profile(root, mock_xai):
    from arclya2a.orchestrator.engine import Orchestrator

    orchestrator = Orchestrator(root, xai_client=mock_xai)
    result = orchestrator.run_chain(
        chain=["onboarding_specialist"],
        initial_ssot={"deal_id": "ok", "summary": "Valid onboarding", "stage": "new", "metadata": {}},
        task_context="Complete onboarding with full profile",
        auto_route=False,
    )
    handoff = result.handoff_chain[0]
    assert handoff["payload"]["onboarding_complete"] is True
    assert result.final_ssot["metadata"]["product_profile_complete"] is True
    assert "destination_cta" in result.final_ssot["metadata"]