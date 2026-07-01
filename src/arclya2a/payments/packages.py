"""Agent-facing USDC service packages and checkout helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from arclya2a.settings import project_root

AGENT_PAYMENTS_DOC_URL = (
    "https://github.com/manhatton31-svg/arclya2a/blob/master/docs/agent-payments.md"
)

# Human-readable USDC network labels for agent-facing discovery copy.
USDC_NETWORK_LABELS: dict[str, str] = {
    "base": "Base",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "bnb": "BSC",
}

USDC_NETWORKS_SUMMARY = "USDC on Base, Ethereum, Solana, and BSC"

PACKAGE_ALIASES: dict[str, str] = {
    "onboarding": "onboarding_package",
    "onboarding_package": "onboarding_package",
    "closer": "closer_access",
    "closer_access": "closer_access",
    "close": "per_close",
    "per_close": "per_close",
}


def _packages_path(root: Path | None = None) -> Path:
    base = root or project_root()
    return base / "pricing" / "agent_payment_packages.json"


@lru_cache(maxsize=1)
def _load_catalog_cached(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_payment_packages_catalog(root: Path | None = None) -> dict[str, Any]:
    """Load the agent payment packages catalog."""
    path = _packages_path(root)
    if not path.is_file():
        return {"version": "1.0.0", "currency": "USDC", "packages": []}
    return _load_catalog_cached(str(path.resolve()))


def list_payment_packages(root: Path | None = None) -> list[dict[str, Any]]:
    """Return checkout-ready package definitions."""
    catalog = load_payment_packages_catalog(root)
    packages = catalog.get("packages") or []
    return [dict(pkg) for pkg in packages if isinstance(pkg, dict)]


def resolve_package_id(package_or_service: str) -> str | None:
    """Resolve package id or service_type alias to canonical package id."""
    key = (package_or_service or "").strip().lower().replace("-", "_")
    if not key:
        return None
    return PACKAGE_ALIASES.get(key)


def get_payment_package(package_or_service: str, root: Path | None = None) -> dict[str, Any] | None:
    """Look up a package by id or alias."""
    package_id = resolve_package_id(package_or_service)
    if not package_id:
        return None
    for pkg in list_payment_packages(root):
        if pkg.get("id") == package_id:
            return pkg
    return None


def build_package_checkout_instructions(
    payment: dict[str, Any],
    package: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    """Human- and agent-readable payment instructions for a package checkout."""
    payment_id = payment.get("payment_id", "")
    amount = payment.get("amount", payment.get("amount_usd"))
    currency = payment.get("currency", payment.get("token", "USDC"))
    network = payment.get("network", "")
    wallet = payment.get("wallet_address", "")
    memo = payment.get("memo")
    base = base_url.rstrip("/")

    explorers = {
        "base": "https://basescan.org/address/",
        "ethereum": "https://etherscan.io/address/",
        "bnb": "https://bscscan.com/address/",
        "solana": "https://solscan.io/account/",
    }
    explorer = explorers.get(str(network).lower(), "")

    steps = [
        f"Send exactly {amount} {currency} on {network} to {wallet}",
    ]
    if memo:
        steps.append(f"Include memo if your wallet supports it: {memo}")
    steps.extend([
        f"POST {base}/payments/crypto/{payment_id}/submit with {{\"tx_hash\": \"<your_tx_hash>\"}}",
        f"Poll GET {base}/payments/crypto/{payment_id} until status is confirmed (200, not 402)",
        "After confirmation, use your production or sandbox key to start the purchased service via handoff-chain",
    ])

    return {
        "summary": (
            f"Pay ${float(amount):.2f} {currency} on {network} for {package.get('name', 'Arclya service')}"
        ),
        "package_id": package.get("id"),
        "package_name": package.get("name"),
        "package_description": package.get("description"),
        "what_you_get": package.get("includes") or [],
        "steps": steps,
        "send": {
            "amount": amount,
            "currency": currency,
            "network": network,
            "wallet_address": wallet,
            "memo": memo,
        },
        "submit_url": f"{base}/payments/crypto/{payment_id}/submit",
        "status_url": f"{base}/payments/crypto/{payment_id}",
        "networks_url": f"{base}/payments/crypto/networks",
        "packages_url": f"{base}/payments/crypto/packages",
        "documentation_url": AGENT_PAYMENTS_DOC_URL,
        "explorer_url": f"{explorer}{wallet}" if explorer and wallet else None,
    }


def package_public_view(package: dict[str, Any]) -> dict[str, Any]:
    """Checkout-safe package summary."""
    return {
        "id": package.get("id"),
        "name": package.get("name"),
        "description": package.get("description"),
        "amount_usd": package.get("amount_usd"),
        "billing": package.get("billing"),
        "includes": package.get("includes") or [],
        "note": package.get("note"),
    }


def build_agent_payments_discovery(
    *,
    base_url: str,
    networks: list[dict[str, Any]],
    root: Path | None = None,
) -> dict[str, Any]:
    """Discovery block for Agent Card and status endpoints."""
    catalog = load_payment_packages_catalog(root)
    base = base_url.rstrip("/")
    packages = list_payment_packages(root)
    return {
        "enabled": True,
        "token": catalog.get("currency", "USDC"),
        "summary": USDC_NETWORKS_SUMMARY,
        "recommended_network": catalog.get("recommended_network", "base"),
        "network_labels": USDC_NETWORK_LABELS,
        "networks": networks,
        "packages": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "description": p.get("description"),
                "amount_usd": p.get("amount_usd"),
                "billing": p.get("billing"),
            }
            for p in packages
        ],
        "checkout": {
            "packages_url": f"{base}/payments/crypto/packages",
            "checkout_url": f"{base}/payments/crypto/checkout",
            "intent_url": f"{base}/payments/crypto/intent",
            "networks_url": f"{base}/payments/crypto/networks",
            "submit_url_pattern": f"{base}/payments/crypto/{{payment_id}}/submit",
            "status_url_pattern": f"{base}/payments/crypto/{{payment_id}}",
            "documentation": AGENT_PAYMENTS_DOC_URL,
        },
        "flow": [
            "discover_packages",
            "create_checkout",
            "send_usdc_on_chain",
            "submit_tx_hash",
            "await_operator_confirm",
            "start_service",
        ],
    }