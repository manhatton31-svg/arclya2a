"""Tests for agent-facing USDC package catalog and checkout helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from arclya2a.payments.packages import (
    USDC_NETWORKS_SUMMARY,
    get_payment_package,
    list_payment_packages,
    resolve_package_id,
)


def test_list_payment_packages_includes_three_services(root):
    packages = list_payment_packages(root)
    ids = {p["id"] for p in packages}
    assert ids == {"onboarding_package", "closer_access", "per_close"}
    amounts = {p["id"]: p["amount_usd"] for p in packages}
    assert amounts["onboarding_package"] == 49.0
    assert amounts["closer_access"] == 99.0
    assert amounts["per_close"] == 25.0


def test_resolve_package_aliases():
    assert resolve_package_id("onboarding") == "onboarding_package"
    assert resolve_package_id("closer") == "closer_access"
    assert resolve_package_id("per_close") == "per_close"
    assert resolve_package_id("unknown") is None


def test_get_payment_package_by_alias(root):
    pkg = get_payment_package("closer", root)
    assert pkg is not None
    assert pkg["id"] == "closer_access"
    assert pkg["name"] == "Closer Access"


def test_usdc_networks_summary_covers_four_chains():
    assert "Base" in USDC_NETWORKS_SUMMARY
    assert "Ethereum" in USDC_NETWORKS_SUMMARY
    assert "Solana" in USDC_NETWORKS_SUMMARY
    assert "BSC" in USDC_NETWORKS_SUMMARY