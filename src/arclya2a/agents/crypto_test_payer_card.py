"""Agent Card for the Arclya Crypto Test Payer — accepts USDC and exercises Arclya checkout."""

from __future__ import annotations

from typing import Any

from arclya2a.payments.crypto import is_crypto_payments_configured, list_accepted_crypto_networks
from arclya2a.payments.packages import AGENT_PAYMENTS_DOC_URL, USDC_NETWORKS_SUMMARY, list_payment_packages
from arclya2a.settings import get_settings, project_root


def _default_base_url() -> str:
    settings = get_settings()
    root = project_root()
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        import json

        fallback = json.load(f)["server"]["base_url"]
    return (settings.resolved_public_url(fallback=fallback) or fallback).rstrip("/")


def resolve_test_payer_wallet() -> str:
    """Public receive wallet for the test payer agent (no private keys)."""
    import os

    explicit = os.environ.get("ARCLYA_CRYPTO_TEST_PAYER_WALLET", "").strip()
    if explicit:
        return explicit
    cfg = get_settings().crypto
    return cfg.wallet_for(cfg.default_network) or cfg.wallet_address or ""


def resolve_refund_address() -> str:
    import os

    explicit = os.environ.get("ARCLYA_CRYPTO_TEST_PAYER_REFUND_ADDRESS", "").strip()
    if explicit:
        return explicit
    return resolve_test_payer_wallet()


def build_crypto_test_payer_agent_card(*, base_url: str | None = None) -> dict[str, Any]:
    """Build A2A Agent Card for the crypto test payer agent."""
    base = (base_url or _default_base_url()).rstrip("/")
    wallet = resolve_test_payer_wallet()
    refund = resolve_refund_address()
    networks = list_accepted_crypto_networks() if is_crypto_payments_configured() else []
    packages = list_payment_packages()
    default_package = "per_close"
    default_amount = next(
        (p.get("amount_usd") for p in packages if p.get("id") == default_package),
        25.0,
    )

    card_url = f"{base}/agents/crypto-test-payer/.well-known/agent-card.json"
    return {
        "name": "Arclya Crypto Test Payer",
        "description": (
            "Autonomous test agent that accepts USDC on Base, Ethereum, Solana, and BSC, "
            "pays Arclya via package checkout to validate the agent payment flow, and refunds "
            "any surplus USDC to the configured refund address. Use for end-to-end crypto "
            "payment rehearsals without manual operator steps beyond optional confirmation."
        ),
        "url": f"{base}/agents/crypto-test-payer",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "usdc_acceptance",
                "name": "USDC Acceptance",
                "description": "Accept USDC deposits on Base, Ethereum, Solana, and BSC.",
                "tags": ["crypto", "usdc", "payments", "base", "ethereum", "solana", "bsc"],
            },
            {
                "id": "arclya_checkout_test",
                "name": "Arclya Checkout Test",
                "description": (
                    "Create Arclya package checkout, submit on-chain proof, and request operator "
                    "confirmation to validate the production payment flow."
                ),
                "tags": ["checkout", "x402", "arclya", "integration_test"],
            },
            {
                "id": "usdc_refund",
                "name": "USDC Refund",
                "description": "Refund surplus USDC to the configured refund address after checkout test.",
                "tags": ["refund", "usdc"],
            },
        ],
        "payments": {
            "accepts": USDC_NETWORKS_SUMMARY,
            "token": "USDC",
            "recommended_network": "base",
            "receive_wallets": networks,
            "primary_wallet": wallet,
            "refund_address": refund,
            "default_test_package": default_package,
            "default_test_amount_usd": default_amount,
            "arclya_checkout_url": f"{base}/payments/crypto/checkout",
            "arclya_packages_url": f"{base}/payments/crypto/packages",
        },
        "documentation": [
            {
                "rel": "agent-payments",
                "type": "markdown",
                "title": "Arclya Agent Payments Guide",
                "href": AGENT_PAYMENTS_DOC_URL,
            },
            {
                "rel": "arclya-platform",
                "type": "api",
                "title": "Arclya Platform Agent Card",
                "href": f"{base}/.well-known/agent-card.json",
            },
            {
                "rel": "test-runner",
                "type": "cli",
                "title": "Crypto test payer CLI",
                "href": "python scripts/crypto_test_payer_agent.py",
            },
        ],
        "endpoints": {
            "agent_card": card_url,
            "fund_instructions": f"{base}/agents/crypto-test-payer/fund-instructions",
            "arclya_checkout": f"{base}/payments/crypto/checkout",
            "arclya_packages": f"{base}/payments/crypto/packages",
        },
        "authentication": {
            "type": "none",
            "note": "Public agent card; Arclya checkout calls use sandbox or production keys via CLI.",
        },
    }