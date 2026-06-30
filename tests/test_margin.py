from arclya2a.pricing.margin import check_margin_guardrail, compute_margin_percent


def test_compute_margin():
    assert compute_margin_percent(100, 20) == 80.0
    assert compute_margin_percent(0, 10) == -100.0


def test_margin_guardrail_approves(root):
    result = check_margin_guardrail(root, revenue_usd=49.0, cost_usd=5.0)
    assert result.approved is True
    assert result.margin_percent > 15


def test_margin_guardrail_vetoes(root):
    result = check_margin_guardrail(root, revenue_usd=10.0, cost_usd=9.5)
    assert result.approved is False
    assert result.veto_reason is not None