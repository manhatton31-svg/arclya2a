"""HTTP routes for the Crypto Test Payer agent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from arclya2a.agents.crypto_test_payer_card import (
    build_crypto_test_payer_agent_card,
    resolve_refund_address,
    resolve_test_payer_wallet,
)
from arclya2a.payments.packages import get_payment_package, list_payment_packages


def register_crypto_test_payer_routes(router: APIRouter) -> None:
    """Register crypto test payer agent discovery endpoints."""

    @router.get("/agents/crypto-test-payer/.well-known/agent-card.json")
    async def crypto_test_payer_agent_card(request: Request) -> JSONResponse:
        base_url = str(request.base_url).rstrip("/")
        return JSONResponse(content=build_crypto_test_payer_agent_card(base_url=base_url))

    @router.get("/agents/crypto-test-payer/fund-instructions")
    async def crypto_test_payer_fund_instructions(
        request: Request,
        package: str = "per_close",
    ) -> dict[str, Any]:
        """Tell funders where to send USDC and how the round-trip test works."""
        base_url = str(request.base_url).rstrip("/")
        root = request.app.state.root
        package_row = get_payment_package(package, root) or get_payment_package("per_close", root) or {}
        package_id = package_row.get("id", "per_close")
        amount = float(package_row.get("amount_usd", 25.0))
        buffer_usd = 2.0
        suggested = round(amount + buffer_usd, 2)

        return {
            "agent": "Arclya Crypto Test Payer",
            "summary": (
                f"Send at least ${suggested:.2f} USDC on Base (or another supported network), "
                f"then run the test payer CLI to pay Arclya ${amount:.2f} and refund the surplus."
            ),
            "receive_wallet": resolve_test_payer_wallet(),
            "refund_address": resolve_refund_address(),
            "recommended_network": "base",
            "suggested_deposit_usd": suggested,
            "buffer_usd": buffer_usd,
            "test_package": package_id,
            "test_amount_usd": amount,
            "packages": [p.get("id") for p in list_payment_packages(root)],
            "steps": [
                f"python scripts/crypto_test_payer_agent.py checkout --network base --package {package_id}",
                f"Send >= ${suggested:.2f} USDC to receive_wallet on your chosen network",
                f"python scripts/crypto_test_payer_agent.py run --network base --package {package_id}",
                "CLI submits tx proof, confirms payment, polls status, refunds surplus (optional)",
            ],
            "agent_card": f"{base_url}/agents/crypto-test-payer/.well-known/agent-card.json",
            "arclya_checkout": f"{base_url}/payments/crypto/checkout",
        }