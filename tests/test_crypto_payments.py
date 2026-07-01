"""Tests for crypto payment tracking."""

from __future__ import annotations

import uuid

import pytest

from arclya2a.payments.crypto import (
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_PENDING,
    create_crypto_payment_intent,
    crypto_payments_summary,
    get_crypto_payment,
    get_crypto_payment_intent,
    get_crypto_payments_by_deal,
    get_crypto_payments_by_partner,
    list_accepted_crypto_networks,
    list_crypto_payment_intents,
    list_crypto_payments,
    record_crypto_payment,
    update_crypto_payment,
    update_crypto_payment_intent,
)
from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.observability.ops_status import build_ops_status


def _enable_crypto(monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORKS", "base,ethereum,solana,bnb")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ETHEREUM", "0xEth742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_SOLANA", "So11111111111111111111111111111111111111112")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BNB", "0xBnb742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("ARCLYA_CRYPTO_MIN_AMOUNT_USD", "5")


def test_record_crypto_payment(root, monkeypatch):
    _enable_crypto(monkeypatch)
    partner_id = f"tp_{uuid.uuid4().hex[:8]}"
    deal_id = f"deal_{uuid.uuid4().hex[:6]}"
    payment = record_crypto_payment(
        root,
        amount=49.0,
        network="base",
        partner_id=partner_id,
        deal_id=deal_id,
        agent_id="agent_alpha",
    )
    assert payment.payment_id.startswith("cpay_")
    assert payment.status == STATUS_PENDING
    assert payment.currency == "USDC"
    assert payment.amount == 49.0
    assert payment.network == "base"
    assert payment.wallet_address.startswith("0x")
    assert payment.memo

    fetched = get_crypto_payment(root, payment.payment_id)
    assert fetched is not None
    assert fetched["partner_id"] == partner_id
    assert fetched["deal_id"] == deal_id


def test_query_payments_by_partner_and_deal(root, monkeypatch):
    _enable_crypto(monkeypatch)
    partner_id = f"tp_{uuid.uuid4().hex[:8]}"
    deal_id = f"deal_{uuid.uuid4().hex[:6]}"
    payment = record_crypto_payment(
        root,
        amount=20.0,
        partner_id=partner_id,
        deal_id=deal_id,
    )

    by_partner = get_crypto_payments_by_partner(root, partner_id)
    assert any(r["payment_id"] == payment.payment_id for r in by_partner)

    by_deal = get_crypto_payments_by_deal(root, deal_id)
    assert any(r["payment_id"] == payment.payment_id for r in by_deal)

    assert list_crypto_payments(root, status=STATUS_PENDING)


def test_update_crypto_payment_statuses(root, monkeypatch):
    _enable_crypto(monkeypatch)
    payment = record_crypto_payment(root, amount=12.0)
    submitted = update_crypto_payment(
        root,
        payment.payment_id,
        status="submitted",
        tx_hash="0xabc123",
    )
    assert submitted["status"] == "submitted"
    assert submitted["tx_hash"] == "0xabc123"

    confirmed = update_crypto_payment(
        root,
        payment.payment_id,
        status=STATUS_CONFIRMED,
        tx_hash="0xabc123",
    )
    assert confirmed["status"] == STATUS_CONFIRMED
    assert confirmed["confirmed_at"]

    failed = update_crypto_payment(root, payment.payment_id, status=STATUS_FAILED)
    assert failed["status"] == STATUS_FAILED


def test_create_crypto_payment_intent(root, monkeypatch):
    _enable_crypto(monkeypatch)
    intent = create_crypto_payment_intent(
        root,
        amount_usd=25.0,
        partner_id="tp_test",
        deal_id="deal_1",
    )
    assert intent.intent_id.startswith("cpi_")
    assert intent.payment_id.startswith("cpay_")
    assert intent.status == STATUS_PENDING
    assert intent.token == "USDC"
    assert intent.network == "base"
    assert intent.wallet_address.startswith("0x")
    assert intent.memo
    assert intent.expires_at

    payment = get_crypto_payment(root, intent.payment_id)
    assert payment is not None
    assert payment["intent_id"] == intent.intent_id


def test_create_intent_requires_configuration(root, monkeypatch):
    for key in (
        "ARCLYA_CRYPTO_WALLET_ADDRESS",
        "ARCLYA_CRYPTO_WALLET_BASE",
        "ARCLYA_CRYPTO_WALLET_ETHEREUM",
        "ARCLYA_CRYPTO_WALLET_SOLANA",
        "ARCLYA_CRYPTO_WALLET_BNB",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    with pytest.raises(ValueError, match="not configured"):
        create_crypto_payment_intent(root, amount_usd=10.0)


def test_create_intent_requires_enabled(root, monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ADDRESS", "0xabc")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "0")
    with pytest.raises(ValueError, match="disabled"):
        create_crypto_payment_intent(root, amount_usd=10.0)


def test_update_intent_syncs_payment(root, monkeypatch):
    _enable_crypto(monkeypatch)
    partner_id = f"tp_{uuid.uuid4().hex[:8]}"
    intent = create_crypto_payment_intent(root, amount_usd=15.0, partner_id=partner_id)
    updated = update_crypto_payment_intent(
        root,
        intent.intent_id,
        status=STATUS_CONFIRMED,
        submitted_tx_hash="0xdeadbeef",
    )
    assert updated["status"] == STATUS_CONFIRMED
    assert updated["tx_hash"] == "0xdeadbeef"
    assert updated["confirmed_at"]

    rows = list_crypto_payment_intents(root, partner_id=partner_id)
    assert any(r["intent_id"] == intent.intent_id and r["status"] == STATUS_CONFIRMED for r in rows)

    payment = get_crypto_payment(root, intent.payment_id)
    assert payment is not None
    assert payment["status"] == STATUS_CONFIRMED


def test_create_intent_on_each_network(root, monkeypatch):
    _enable_crypto(monkeypatch)
    for network in ("base", "ethereum", "solana", "bnb"):
        intent = create_crypto_payment_intent(root, amount_usd=10.0, network=network)
        assert intent.network == network
        assert intent.wallet_address

    accepted = list_accepted_crypto_networks()
    assert {opt["network"] for opt in accepted} == {"base", "ethereum", "solana", "bnb"}


def test_create_intent_rejects_unconfigured_network(root, monkeypatch):
    _enable_crypto(monkeypatch)
    monkeypatch.delenv("ARCLYA_CRYPTO_WALLET_SOLANA", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        create_crypto_payment_intent(root, amount_usd=10.0, network="solana")


def test_crypto_payments_summary_and_ops_status(root, monkeypatch):
    _enable_crypto(monkeypatch)
    before = crypto_payments_summary(root)
    create_crypto_payment_intent(root, amount_usd=10.0)
    summary = crypto_payments_summary(root)
    assert summary["configured"] is True
    assert summary["enabled"] is True
    assert summary["payment_count"] == before["payment_count"] + 1
    assert summary["intent_count"] == before["intent_count"] + 1

    status = build_ops_status(root)
    assert "payments" in status
    assert status["payments"]["payment_count"] == before["payment_count"] + 1

    dashboard = build_ops_dashboard(root)
    assert dashboard["payments"]["network"] == "base"
    assert dashboard["payments"]["token"] == "USDC"
    assert set(dashboard["payments"]["networks"]) == {"base", "ethereum", "solana", "bnb"}