"""Tests for scripts/confirm_crypto_payment.py."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import confirm_crypto_payment as cli  # noqa: E402
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


def test_fetch_pending_review_lists_submitted_payment(cli_client):
    created = cli_client.post(
        "http://testserver/payments/crypto/intent",
        json={"amount": 22.0, "partner_id": "tp_cli_test", "deal_id": "deal_cli"},
    )
    assert created.status_code == 201
    payment_id = created.json()["payment"]["payment_id"]
    cli_client.post(
        f"http://testserver/payments/crypto/{payment_id}/submit",
        json={"tx_hash": "0xclisubmit123456"},
    )

    summary = cli.fetch_pending_review(cli_client, "http://testserver")
    assert summary["pending_review_count"] >= 1
    ids = {row["payment_id"] for row in summary["pending_review"]}
    assert payment_id in ids


def test_format_pending_list_includes_summary():
    text = cli.format_pending_list(
        {
            "pending_review_count": 1,
            "by_status": {"pending": 0, "submitted": 1, "confirmed": 0, "failed": 0},
            "confirmed_total_usd": 0,
            "pending_review": [],
        },
        enriched=[
            {
                "payment_id": "cpay_test123",
                "partner_id": "tp_a",
                "agent_label": "AgentA",
                "amount": 10.0,
                "currency": "USDC",
                "network": "base",
                "status": "submitted",
                "age": "1h",
                "tx_hash": "0xabc",
            }
        ],
    )
    assert "Needs review:  1" in text
    assert "cpay_test123" in text
    assert "submitted" in text


def test_confirm_payment_remote_success(cli_client):
    created = cli_client.post(
        "http://testserver/payments/crypto/intent",
        json={"amount": 33.0},
    )
    payment_id = created.json()["payment"]["payment_id"]
    cli_client.post(
        f"http://testserver/payments/crypto/{payment_id}/submit",
        json={"tx_hash": "0xbeforeconfirm"},
    )

    result = cli.confirm_payment_remote(
        cli_client,
        "http://testserver",
        payment_id,
        operator_key="operator-test-secret-key",
        tx_hash="0xverifiedfinal",
        confirmed_by="cli_test",
    )
    assert result["payment"]["status"] == "confirmed"
    assert result["payment"]["tx_hash"] == "0xverifiedfinal"

    get_resp = cli_client.get(f"http://testserver/payments/crypto/{payment_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["payment"]["status"] == "confirmed"


def test_main_list_mode(cli_client, capsys):
    cli_client.post("http://testserver/payments/crypto/intent", json={"amount": 12.0})
    code = cli.main(
        ["--base-url", "http://testserver", "--operator-key", "operator-test-secret-key"],
        http_client=cli_client,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Needs review:" in out
    assert "cpay_" in out


def test_main_confirm_mode(cli_client, capsys):
    created = cli_client.post("http://testserver/payments/crypto/intent", json={"amount": 18.0})
    payment_id = created.json()["payment"]["payment_id"]
    code = cli.main(
        [
            "--confirm",
            payment_id,
            "--tx-hash",
            "0xmainconfirm",
            "--confirmed-by",
            "pytest",
            "--base-url",
            "http://testserver",
            "--operator-key",
            "operator-test-secret-key",
        ],
        http_client=cli_client,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Crypto Payment Confirmed" in out
    assert payment_id in out