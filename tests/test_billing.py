import uuid

from arclya2a.billing.tracker import ClosedDealRecord, billing_summary, list_closed_deals, record_closed_deal


def test_record_closed_deal(root):
    deal_id = f"deal_bill_{uuid.uuid4().hex[:8]}"
    record = ClosedDealRecord(
        deal_id=deal_id,
        close_type="lead_routing_commitment",
        revenue_usd=99.0,
        cost_usd=12.0,
        margin_percent=87.88,
        affiliate_code="partner_42",
        cta_url="https://example.com/signup?ref=partner_42",
        product_name="Lead Router",
    )
    result = record_closed_deal(root, record)
    assert result["deal_id"] == deal_id
    assert result["billing_model"] == "success_based"
    assert result["duplicate"] is False

    again = record_closed_deal(root, record)
    assert again["duplicate"] is True

    deals = list_closed_deals(root, deal_id=deal_id)
    assert len(deals) == 1
    assert deals[0]["affiliate_code"] == "partner_42"


def test_billing_summary(root):
    record_closed_deal(
        root,
        ClosedDealRecord(
            deal_id="deal_sum_001",
            close_type="lead_routing_commitment",
            revenue_usd=50.0,
            cost_usd=10.0,
            margin_percent=80.0,
            affiliate_code="aff_a",
            cta_url="https://example.com/a",
            product_name="Product A",
        ),
    )
    summary = billing_summary(root)
    assert summary["deal_count"] >= 1
    assert summary["billing_model"] == "success_based"
    assert summary["total_revenue_usd"] >= 50.0