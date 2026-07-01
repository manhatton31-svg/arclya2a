"""Tests for crypto_test_payer_agent script helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crypto_test_payer_agent as agent  # noqa: E402


def test_suggested_deposit_adds_buffer():
    assert agent.suggested_deposit(25.0, buffer_usd=2.0) == 27.0
    assert agent.suggested_deposit(49.0, buffer_usd=2.0) == 51.0


def test_fetch_recent_usdc_inbound_filters_amount(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "to": {"hash": "0xABC"},
                        "from": {"hash": "0xFROM"},
                        "token": {"symbol": "USDC", "decimals": "6"},
                        "total": {"value": "1000000"},
                        "transaction_hash": "0xsmall",
                        "timestamp": "2026-07-01T20:00:00Z",
                    },
                    {
                        "to": {"hash": "0xabc"},
                        "from": {"hash": "0xFROM2"},
                        "token": {"symbol": "USDC", "decimals": "6"},
                        "total": {"value": "25000000"},
                        "transaction_hash": "0xbig",
                        "timestamp": "2026-07-01T20:01:00Z",
                    },
                ]
            }

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    rows = agent.fetch_recent_usdc_inbound("0xABC", min_amount=25.0)
    assert len(rows) == 1
    assert rows[0]["tx_hash"] == "0xbig"
    assert rows[0]["amount"] == 25.0