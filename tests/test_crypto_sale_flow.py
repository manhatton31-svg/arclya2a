"""Tests for scripts/crypto_sale_flow.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crypto_sale_flow as flow  # noqa: E402
import run_first_crypto_sale as cli  # noqa: E402
from arclya2a.server.app import create_app


class _TestClientAdapter:
    def __init__(self, client: TestClient):
        self._client = client

    def get(self, url: str, *, headers=None):
        path = url.replace("http://testserver", "")
        return self._client.get(path, headers=headers)

    def post(self, url: str, *, json=None, headers=None):
        path = url.replace("http://testserver", "")
        return self._client.post(path, json=json, headers=headers)


@pytest.fixture
def flow_client(root, mock_xai, monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORKS", "base,ethereum")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("ARCLYA_CRYPTO_MIN_AMOUNT_USD", "1")
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")
    client = TestClient(create_app(root, xai_client=mock_xai))
    return _TestClientAdapter(client)


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    path = tmp_path / "crypto_sale_flow.json"
    monkeypatch.setattr(flow, "STATE_PATH", path)
    return path


def test_sale_start_persists_state(flow_client, state_path):
    result = flow.run_sale_start(
        flow_client,
        base_url="http://testserver",
        partner_id="tp_sale_test",
        amount_usd=1.0,
        network="base",
        deal_id="deal_sale_1",
    )
    assert result["ok"] is True
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["step"] == "awaiting_onchain_payment"
    assert state["payment_id"]
    assert state["wallet_address"]


def test_sale_submit_and_confirm(flow_client, state_path, monkeypatch):
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")
    flow.run_sale_start(
        flow_client,
        base_url="http://testserver",
        partner_id="tp_sale_test",
        amount_usd=1.0,
    )
    state = flow.load_state()
    pid = state["payment_id"]
    submit = flow.run_sale_submit(flow_client, "0xabc123def4567890", base_url="http://testserver")
    assert submit["ok"] is True
    assert flow.load_state()["step"] == "awaiting_operator_confirm"

    confirm = flow.run_sale_confirm(flow_client, base_url="http://testserver", payment_id=pid)
    assert confirm["ok"] is True
    assert flow.load_state()["step"] == "confirmed"


def test_cli_sale_start(flow_client, state_path, monkeypatch):
    monkeypatch.setattr(flow, "STATE_PATH", state_path)
    code = cli.main(
        ["sale", "start", "--partner-id", "tp_cli", "--amount", "1", "--network", "base"],
        http_client=flow_client,
    )
    assert code == 0
    assert state_path.is_file()