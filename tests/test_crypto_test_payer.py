"""Tests for Crypto Test Payer agent card and fund instructions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.crypto_test_payer_card import build_crypto_test_payer_agent_card
from arclya2a.server.app import create_app


@pytest.fixture
def client(root, mock_xai, monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORKS", "base,ethereum,solana,bnb")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ETHEREUM", "0xEth742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_SOLANA", "So11111111111111111111111111111111111111112")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BNB", "0xBnb742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    return TestClient(create_app(root, xai_client=mock_xai))


def test_crypto_test_payer_agent_card():
    card = build_crypto_test_payer_agent_card(base_url="https://example.com")
    assert card["name"] == "Arclya Crypto Test Payer"
    assert "USDC" in card["description"]
    assert "Solana" in card["description"]
    assert card["payments"]["default_test_package"] == "per_close"
    assert card["endpoints"]["arclya_checkout"].endswith("/payments/crypto/checkout")


def test_crypto_test_payer_agent_card_endpoint(client):
    resp = client.get("/agents/crypto-test-payer/.well-known/agent-card.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skills"]
    assert data["payments"]["primary_wallet"].startswith("0x")


def test_crypto_test_payer_fund_instructions(client):
    resp = client.get("/agents/crypto-test-payer/fund-instructions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["receive_wallet"]
    assert data["test_package"] == "per_close"
    assert data["suggested_deposit_usd"] >= data["test_amount_usd"]
    assert any("crypto_test_payer_agent.py" in step for step in data["steps"])