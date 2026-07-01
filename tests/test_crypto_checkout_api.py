"""Tests for x402-enabled crypto checkout HTTP API."""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.audit.logger import read_audit_records
from arclya2a.observability.dashboard import build_ops_dashboard, format_ops_dashboard_text
from arclya2a.payments.crypto import STATUS_CONFIRMED, crypto_payments_summary, update_crypto_payment
from arclya2a.server.app import create_app


def _enable_crypto(monkeypatch):
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORKS", "base,ethereum,solana,bnb")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ETHEREUM", "0xEth742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_SOLANA", "So11111111111111111111111111111111111111112")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BNB", "0xBnb742d35Cc6634C0532925a3b844Bc9e7595f0bEb0")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")
    monkeypatch.setenv("ARCLYA_CRYPTO_MIN_AMOUNT_USD", "5")


@pytest.fixture
def client(root, mock_xai, monkeypatch):
    _enable_crypto(monkeypatch)
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "operator-test-secret-key")
    return TestClient(create_app(root, xai_client=mock_xai))


OPERATOR_HEADERS = {"X-Arclya-Operator-Key": "operator-test-secret-key"}


def test_create_intent_returns_201_with_x402_headers(client):
    resp = client.post(
        "/payments/crypto/intent",
        json={
            "amount": 25.0,
            "network": "base",
            "partner_id": "tp_agent",
            "deal_id": "deal_checkout_1",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["payment_required"] is True
    assert data["payment"]["payment_id"].startswith("cpay_")
    assert data["payment"]["status"] == "pending"
    assert data["x402"]["x402Version"] == 2

    assert resp.headers.get("X-Payment-Required") == "true"
    assert resp.headers.get("X-Payment-Network") == "base"
    assert resp.headers.get("X-Payment-Amount") == "25.0"
    assert resp.headers.get("X-Payment-Address", "").startswith("0x")
    assert resp.headers.get("PAYMENT-REQUIRED")

    details = json.loads(resp.headers["X-Payment-Required-Details"])
    assert details["accepts"][0]["scheme"] == "exact"
    assert details["accepts"][0]["payTo"].startswith("0x")


def test_create_intent_prefers_402(client):
    resp = client.post(
        "/payments/crypto/intent",
        json={"amount": 10.0},
        headers={"X-Arclya-Prefer-402": "true"},
    )
    assert resp.status_code == 402
    assert resp.headers.get("X-Payment-Required") == "true"


def test_get_payment_returns_402_when_pending(client):
    created = client.post("/payments/crypto/intent", json={"amount": 12.0})
    payment_id = created.json()["payment"]["payment_id"]

    resp = client.get(f"/payments/crypto/{payment_id}")
    assert resp.status_code == 402
    assert resp.json()["payment_required"] is True
    assert resp.headers.get("X-Payment-Id") == payment_id


def test_get_payment_returns_200_when_confirmed(client, root):
    created = client.post("/payments/crypto/intent", json={"amount": 15.0})
    payment_id = created.json()["payment"]["payment_id"]
    update_crypto_payment(root, payment_id, status=STATUS_CONFIRMED, tx_hash="0xconfirmed")

    resp = client.get(f"/payments/crypto/{payment_id}")
    assert resp.status_code == 200
    assert resp.json()["payment"]["status"] == "confirmed"
    assert resp.headers.get("X-Payment-Required") == "false"


def test_submit_payment_with_body(client):
    created = client.post("/payments/crypto/intent", json={"amount": 20.0})
    payment_id = created.json()["payment"]["payment_id"]

    resp = client.post(
        f"/payments/crypto/{payment_id}/submit",
        json={"tx_hash": "0xabc123def4567890"},
    )
    assert resp.status_code == 200
    assert resp.json()["payment"]["status"] == "submitted"
    assert resp.json()["payment"]["tx_hash"] == "0xabc123def4567890"
    assert resp.headers.get("PAYMENT-RESPONSE")

    settlement = json.loads(base64.b64decode(resp.headers["PAYMENT-RESPONSE"]))
    assert settlement["status"] == "submitted"


def test_submit_payment_with_x_payment_header(client):
    created = client.post("/payments/crypto/intent", json={"amount": 18.0})
    payment_id = created.json()["payment"]["payment_id"]
    proof = json.dumps({"tx_hash": "0xheaderproof123456"})
    resp = client.post(
        f"/payments/crypto/{payment_id}/submit",
        headers={"X-Payment": proof},
    )
    assert resp.status_code == 200
    assert resp.json()["payment"]["tx_hash"] == "0xheaderproof123456"


def test_submit_missing_proof_returns_400(client):
    created = client.post("/payments/crypto/intent", json={"amount": 9.0})
    payment_id = created.json()["payment"]["payment_id"]
    resp = client.post(f"/payments/crypto/{payment_id}/submit")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_payment_proof"


def test_get_payment_not_found(client):
    resp = client.get("/payments/crypto/cpay_doesnotexist")
    assert resp.status_code == 404


def test_create_intent_disabled_returns_503(root, mock_xai, monkeypatch):
    _enable_crypto(monkeypatch)
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "0")
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.post("/payments/crypto/intent", json={"amount": 10.0})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "crypto_disabled"


def test_list_networks(client):
    resp = client.get("/payments/crypto/networks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert len(data["networks"]) == 4
    network_ids = {n["network"] for n in data["networks"]}
    assert network_ids == {"base", "ethereum", "solana", "bnb"}
    assert "Base" in data["summary"]
    assert "Solana" in data["summary"]
    assert "BSC" in data["summary"]


def test_list_packages(client):
    resp = client.get("/payments/crypto/packages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert len(data["packages"]) == 3
    ids = {p["id"] for p in data["packages"]}
    assert "onboarding_package" in ids
    assert "closer_access" in ids
    assert "per_close" in ids
    assert data["checkout_url"].endswith("/payments/crypto/checkout")
    assert "Solana" in data["summary"]


def test_checkout_onboarding_package(client):
    resp = client.post(
        "/payments/crypto/checkout",
        json={"package": "onboarding_package", "network": "base", "agent_id": "test_agent"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["payment_required"] is True
    assert data["package"]["id"] == "onboarding_package"
    assert data["package"]["amount_usd"] == 49.0
    assert data["payment"]["amount"] == 49.0
    assert data["payment"]["network"] == "base"
    assert data["instructions"]["package_name"] == "Onboarding Package"
    assert len(data["instructions"]["steps"]) >= 4
    assert data["instructions"]["what_you_get"]


def test_checkout_service_type_alias(client):
    resp = client.post(
        "/payments/crypto/checkout",
        json={"service_type": "closer", "network": "solana"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["package"]["id"] == "closer_access"
    assert data["payment"]["amount"] == 99.0
    assert data["payment"]["network"] == "solana"


def test_checkout_unknown_package_returns_400(client):
    resp = client.post(
        "/payments/crypto/checkout",
        json={"package": "not_a_real_package"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unknown_package"


def test_checkout_missing_package_returns_400(client):
    resp = client.post("/payments/crypto/checkout", json={})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_package"


def test_confirm_payment_requires_operator_key(client):
    created = client.post("/payments/crypto/intent", json={"amount": 11.0})
    payment_id = created.json()["payment"]["payment_id"]
    resp = client.post(f"/payments/crypto/{payment_id}/confirm")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "operator_authentication_error"


def test_confirm_payment_operator_success(client, root):
    created = client.post("/payments/crypto/intent", json={"amount": 30.0, "deal_id": "sale_1"})
    payment_id = created.json()["payment"]["payment_id"]
    client.post(
        f"/payments/crypto/{payment_id}/submit",
        json={"tx_hash": "0xsubmitproof123456"},
    )

    resp = client.post(
        f"/payments/crypto/{payment_id}/confirm",
        json={"tx_hash": "0xverifiedonchain999", "confirmed_by": "ops_test"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["payment"]["status"] == "confirmed"
    assert data["payment"]["tx_hash"] == "0xverifiedonchain999"
    assert resp.headers.get("X-Payment-Required") == "false"

    get_resp = client.get(f"/payments/crypto/{payment_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["payment"]["status"] == "confirmed"

    audit = read_audit_records(root, limit=20)
    assert any(r.get("action") == "crypto_payment_confirmed" for r in audit)


def test_confirm_payment_idempotent(client):
    created = client.post("/payments/crypto/intent", json={"amount": 14.0})
    payment_id = created.json()["payment"]["payment_id"]
    client.post(
        f"/payments/crypto/{payment_id}/confirm",
        headers=OPERATOR_HEADERS,
    )
    again = client.post(
        f"/payments/crypto/{payment_id}/confirm",
        headers=OPERATOR_HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True


def test_ops_dashboard_shows_crypto_status_counts(client, root):
    client.post("/payments/crypto/intent", json={"amount": 10.0})
    summary = crypto_payments_summary(root)
    assert "by_status" in summary
    assert summary["by_status"]["pending"] >= 1
    assert "pending_review" in summary

    dashboard = build_ops_dashboard(root)
    text = format_ops_dashboard_text(dashboard)
    assert "Pending:" in text
    assert "Submitted:" in text
    assert "Confirmed:" in text
    assert "Needs review:" in text