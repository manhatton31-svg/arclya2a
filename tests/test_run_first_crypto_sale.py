"""Tests for scripts/run_first_crypto_sale.py."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_first_crypto_sale as cli  # noqa: E402
from arclya2a.server.app import create_app


class _TestClientAdapter:
    def __init__(self, client: TestClient):
        self._client = client
        self._base = "http://testserver"

    def _path(self, url: str) -> str:
        return url.replace(self._base, "")

    def get(self, url: str, *, headers=None):
        return self._client.get(self._path(url), headers=headers)

    def post(self, url: str, *, json=None, headers=None):
        return self._client.post(self._path(url), json=json, headers=headers)


def _enable_crypto(monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORKS", "base,ethereum,solana,bnb")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ETHEREUM", "0xEth742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_SOLANA", "So11111111111111111111111111111111111111112")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BNB", "0xBnb742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("ARCLYA_CRYPTO_MIN_AMOUNT_USD", "5")
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")


@pytest.fixture
def cli_client(root, mock_xai, monkeypatch):
    _enable_crypto(monkeypatch)
    client = TestClient(create_app(root, xai_client=mock_xai))
    return _TestClientAdapter(client)


def test_runbook_file_exists():
    assert cli.RUNBOOK_PATH.is_file()


def test_check_remote_health_passes(cli_client, monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")
    ok, steps = cli.check_remote_health(cli_client, "http://testserver")
    assert ok is True
    assert all(s["ok"] for s in steps)


def test_verify_confirmed_payment(cli_client, root, monkeypatch):
    from arclya2a.payments.crypto import STATUS_CONFIRMED, update_crypto_payment

    _enable_crypto(monkeypatch)
    created = cli_client.post(
        "http://testserver/payments/crypto/intent",
        json={
            "amount": 30.0,
            "partner_id": "tp_verify",
            "deal_id": "deal_verify_1",
        },
    )
    payment_id = created.json()["payment"]["payment_id"]
    update_crypto_payment(
        root,
        payment_id,
        status=STATUS_CONFIRMED,
        tx_hash="0xverify123",
    )

    result = cli.run_verify_step(
        cli_client,
        payment_id,
        partner_id="tp_verify",
        deal_id="deal_verify_1",
        base_url="http://testserver",
    )
    assert result["ok"] is True


def test_main_check_command(cli_client, monkeypatch, capsys):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")
    code = cli.main(["check"], http_client=cli_client)
    captured = capsys.readouterr().out
    assert code == 0
    assert "GO" in captured


def test_main_guide_command():
    code = cli.main(["guide"])
    assert code == 0


def test_pending_review_includes_metadata(root, monkeypatch):
    from arclya2a.payments.crypto import crypto_payments_summary, record_crypto_payment

    _enable_crypto(monkeypatch)
    record_crypto_payment(
        root,
        amount=11.0,
        partner_id="tp_meta",
        deal_id="deal_meta",
        metadata={"agent_id": "buyer_agent"},
    )
    summary = crypto_payments_summary(root)
    assert summary["pending_review"]
    assert summary["pending_review"][0].get("metadata", {}).get("agent_id") == "buyer_agent"