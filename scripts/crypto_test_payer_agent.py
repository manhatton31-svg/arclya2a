#!/usr/bin/env python3
"""Crypto Test Payer Agent — accept USDC, pay Arclya, refund surplus.

Usage
-----
    python scripts/crypto_test_payer_agent.py instructions
    python scripts/crypto_test_payer_agent.py register
    python scripts/crypto_test_payer_agent.py run --network base
    python scripts/crypto_test_payer_agent.py run --tx-hash 0x...   # skip deposit detection
    python scripts/crypto_test_payer_agent.py refund --amount 5.0   # manual refund trigger
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

STATE_PATH = ROOT / "data" / "ops" / "crypto_test_payer_state.json"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BLOCKSCOUT_BASE = "https://base.blockscout.com/api/v2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_base_url() -> str:
    return os.environ.get("ARCLYA_BASE_URL", "https://arclya2a.onrender.com").rstrip("/")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"step": "not_started"}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _http_client():
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx required", file=sys.stderr)
        raise SystemExit(2)
    return httpx.Client(timeout=120.0)


def fetch_fund_instructions(client, base_url: str) -> dict[str, Any]:
    resp = client.get(f"{base_url}/agents/crypto-test-payer/fund-instructions")
    resp.raise_for_status()
    return resp.json()


def print_instructions(data: dict[str, Any]) -> None:
    print("=" * 72)
    print("Crypto Test Payer — Fund Instructions")
    print("=" * 72)
    print(f"  Agent:           {data.get('agent')}")
    print(f"  Summary:         {data.get('summary')}")
    print(f"  Receive wallet:  {data.get('receive_wallet')}")
    print(f"  Refund address:  {data.get('refund_address')}")
    print(f"  Network:         {data.get('recommended_network')} (or ethereum/solana/bnb)")
    print(f"  Suggested send:  {data.get('suggested_deposit_usd')} USDC")
    print(f"  Test package:    {data.get('test_package')} (${data.get('test_amount_usd')})")
    print("")
    print("  After sending USDC, run:")
    print("    python scripts/crypto_test_payer_agent.py run --network base")
    print("=" * 72)


def register_sandbox(client, base_url: str) -> dict[str, Any]:
    card_url = f"{base_url}/agents/crypto-test-payer/.well-known/agent-card.json"
    resp = client.post(
        f"{base_url}/partners/sandbox/register",
        json={
            "agent_name": "Arclya Crypto Test Payer",
            "agent_card_url": card_url,
            "contact_email": "crypto-test-payer@arclya.local",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    state = load_state()
    state.update({
        "step": "registered",
        "partner_id": payload.get("partner_id"),
        "sandbox_key_prefix": (payload.get("api_key") or "")[:24] + "…",
        "agent_card_url": card_url,
    })
    save_state(state)
    print(f"Registered partner_id={payload.get('partner_id')}")
    print(f"Sandbox key: {payload.get('api_key')}")
    print("Save the sandbox key as ARCLYA_SANDBOX_KEY in .env if you need handoff tests.")
    return payload


def _fetch_recent_usdc_inbound(wallet: str, *, since_iso: str | None = None) -> list[dict[str, Any]]:
    import httpx

    url = f"{BLOCKSCOUT_BASE}/addresses/{wallet}/token-transfers?type=ERC-20"
    resp = httpx.get(url, timeout=60.0)
    resp.raise_for_status()
    items = resp.json().get("items") or []
    inbound: list[dict[str, Any]] = []
    for row in items:
        to_addr = (row.get("to") or {}).get("hash", "").lower()
        if to_addr != wallet.lower():
            continue
        token = row.get("token") or {}
        if token.get("symbol") != "USDC":
            continue
        ts = row.get("timestamp") or ""
        if since_iso and ts < since_iso:
            continue
        decimals = int(token.get("decimals") or 6)
        raw = int((row.get("total") or {}).get("value") or 0)
        amount = raw / (10 ** decimals)
        inbound.append({
            "tx_hash": row.get("transaction_hash"),
            "amount": amount,
            "timestamp": ts,
            "from": (row.get("from") or {}).get("hash"),
        })
    return inbound


def create_checkout(
    client,
    base_url: str,
    *,
    package: str,
    network: str,
    partner_id: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"package": package, "network": network}
    if partner_id:
        body["partner_id"] = partner_id
    body["deal_id"] = "crypto_test_payer_001"
    body["agent_id"] = "crypto_test_payer"
    resp = client.post(f"{base_url}/payments/crypto/checkout", json=body)
    resp.raise_for_status()
    return resp.json()


def submit_and_confirm(
    client,
    base_url: str,
    payment_id: str,
    tx_hash: str,
) -> dict[str, Any]:
    import confirm_crypto_payment as crypto_cli
    from arclya2a.server.operator_auth import load_operator_key

    submit_resp = client.post(
        f"{base_url}/payments/crypto/{payment_id}/submit",
        json={"tx_hash": tx_hash},
    )
    submit_resp.raise_for_status()
    operator_key = load_operator_key()
    if not operator_key or len(operator_key) < 8:
        raise RuntimeError("ARCLYA_OPERATOR_KEY required for confirm step")
    confirm = crypto_cli.confirm_payment_remote(
        client,
        base_url,
        payment_id,
        operator_key=operator_key,
        tx_hash=tx_hash,
        confirmed_by="crypto_test_payer",
    )
    return {"submit": submit_resp.json(), "confirm": confirm}


def send_usdc_refund_base(*, to_address: str, amount_usd: float) -> str:
    """Send USDC on Base; requires ARCLYA_CRYPTO_TEST_PAYER_PRIVATE_KEY in env."""
    private_key = os.environ.get("ARCLYA_CRYPTO_TEST_PAYER_PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError(
            "Set ARCLYA_CRYPTO_TEST_PAYER_PRIVATE_KEY in .env to enable automated refunds on Base"
        )
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RuntimeError("pip install web3 for automated refunds") from exc

    from arclya2a.agents.crypto_test_payer_card import resolve_test_payer_wallet

    rpc = os.environ.get("ARCLYA_BASE_RPC_URL", "https://mainnet.base.org")
    w3 = Web3(Web3.HTTPProvider(rpc))
    account = w3.eth.account.from_key(private_key)
    from_wallet = resolve_test_payer_wallet()
    if account.address.lower() != from_wallet.lower():
        raise RuntimeError(
            f"Private key address {account.address} does not match test payer wallet {from_wallet}"
        )

    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(BASE_USDC),
        abi=[
            {
                "constant": False,
                "inputs": [
                    {"name": "_to", "type": "address"},
                    {"name": "_value", "type": "uint256"},
                ],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            }
        ],
    )
    amount_raw = int(round(amount_usd * 1_000_000))
    tx = usdc.functions.transfer(Web3.to_checksum_address(to_address), amount_raw).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 100_000,
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
            "chainId": 8453,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


def run_test(
    *,
    network: str = "base",
    package: str = "per_close",
    tx_hash: str | None = None,
    wait_minutes: int = 30,
    skip_refund: bool = False,
) -> dict[str, Any]:
    base_url = resolve_base_url()
    client = _http_client()
    fund = fetch_fund_instructions(client, base_url)
    wallet = fund["receive_wallet"]
    refund_to = fund["refund_address"]
    amount = float(fund["test_amount_usd"])
    suggested = float(fund["suggested_deposit_usd"])

    state = load_state()
    partner_id = state.get("partner_id")

    print(f"Creating checkout: {package} on {network} …")
    checkout = create_checkout(
        client, base_url, package=package, network=network, partner_id=partner_id
    )
    payment = checkout.get("payment") or {}
    payment_id = payment.get("payment_id")
    if not payment_id:
        raise RuntimeError(f"Checkout missing payment_id: {checkout}")

    created_at = payment.get("created_at") or _now_iso()
    state.update({
        "step": "awaiting_deposit",
        "payment_id": payment_id,
        "checkout": checkout,
        "network": network,
        "package": package,
    })
    save_state(state)

    print(f"  Payment ID: {payment_id}")
    print(f"  Pay exactly: {payment.get('amount')} USDC to {payment.get('wallet_address')}")
    print(checkout.get("instructions", {}).get("summary", ""))

    resolved_tx = tx_hash
    if not resolved_tx:
        if network != "base":
            raise RuntimeError(
                "Auto deposit detection is Base-only; pass --tx-hash for other networks"
            )
        print(f"Watching for inbound USDC to {wallet} (up to {wait_minutes} min) …")
        deadline = time.time() + wait_minutes * 60
        while time.time() < deadline:
            inbound = _fetch_recent_usdc_inbound(wallet, since_iso=created_at)
            for row in inbound:
                if row["amount"] >= amount:
                    resolved_tx = row["tx_hash"]
                    print(f"  Found funding tx: {resolved_tx} ({row['amount']} USDC)")
                    break
            if resolved_tx:
                break
            time.sleep(20)
        if not resolved_tx:
            raise RuntimeError(
                f"No inbound USDC >= {amount} detected. Send >= {suggested} USDC to {wallet} "
                f"or re-run with --tx-hash <hash>"
            )

    print("Submitting proof and confirming …")
    result = submit_and_confirm(client, base_url, payment_id, resolved_tx)
    state.update({
        "step": "confirmed",
        "tx_hash": resolved_tx,
        "confirm": result.get("confirm"),
    })
    save_state(state)
    print(f"  Payment confirmed: {payment_id}")

    refund_amount = max(0.0, suggested - amount)
    if skip_refund or refund_amount <= 0:
        print("Refund skipped.")
        return state

    if network != "base":
        print(f"Manual refund: send {refund_amount} USDC to {refund_to} on {network}")
        return state

    try:
        print(f"Refunding ${refund_amount:.2f} USDC to {refund_to} …")
        refund_tx = send_usdc_refund_base(to_address=refund_to, amount_usd=refund_amount)
        state.update({"step": "refunded", "refund_tx_hash": refund_tx, "refund_amount": refund_amount})
        save_state(state)
        print(f"  Refund tx: {refund_tx}")
    except RuntimeError as exc:
        print(f"  Automated refund unavailable: {exc}")
        print(f"  Manual refund: send ${refund_amount:.2f} USDC to {refund_to} on Base")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arclya Crypto Test Payer Agent")
    parser.add_argument(
        "command",
        choices=["instructions", "register", "run", "refund", "status"],
        help="Agent action",
    )
    parser.add_argument("--network", default="base", help="Network for checkout (default: base)")
    parser.add_argument("--package", default="per_close", help="Arclya package to purchase")
    parser.add_argument("--tx-hash", help="Use this funding tx instead of auto-detect")
    parser.add_argument("--wait-minutes", type=int, default=30, help="Deposit watch timeout")
    parser.add_argument("--amount", type=float, help="Refund amount for refund command")
    parser.add_argument("--skip-refund", action="store_true", help="Skip surplus refund step")
    args = parser.parse_args(argv)

    client = _http_client()
    base_url = resolve_base_url()

    if args.command == "instructions":
        print_instructions(fetch_fund_instructions(client, base_url))
        return 0
    if args.command == "register":
        register_sandbox(client, base_url)
        return 0
    if args.command == "status":
        print(json.dumps(load_state(), indent=2))
        return 0
    if args.command == "refund":
        if not args.amount:
            print("ERROR: --amount required for refund", file=sys.stderr)
            return 2
        from arclya2a.agents.crypto_test_payer_card import resolve_refund_address

        tx = send_usdc_refund_base(to_address=resolve_refund_address(), amount_usd=args.amount)
        print(f"Refund sent: {tx}")
        return 0
    if args.command == "run":
        run_test(
            network=args.network,
            package=args.package,
            tx_hash=args.tx_hash,
            wait_minutes=args.wait_minutes,
            skip_refund=args.skip_refund,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())