#!/usr/bin/env python3
"""Crypto Test Payer Agent — external-agent round-trip test for Arclya USDC checkout.

No funds required for ``checkout`` (dry run). Use ``run`` after you have sent USDC on-chain.

Usage
-----
    # 1. See what to send (no on-chain payment yet)
    python scripts/crypto_test_payer_agent.py checkout --network base --package per_close

    # 2. After sending USDC, run the full flow (or pass --tx-hash)
    python scripts/crypto_test_payer_agent.py run --network base --package per_close
    python scripts/crypto_test_payer_agent.py run --network base --package per_close --tx-hash 0x...

    # Cheapest live test: per_close ($25). onboarding_package is $49.
    python scripts/crypto_test_payer_agent.py instructions --package per_close
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

STATE_PATH = ROOT / "data" / "ops" / "crypto_test_payer_state.json"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BLOCKSCOUT_BASE = "https://base.blockscout.com/api/v2"
DEFAULT_BUFFER_USD = 2.0


class HttpClient(Protocol):
    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any: ...
    def post(self, url: str, *, json: dict[str, Any] | None = None) -> Any: ...


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


def _shared_client():
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx required", file=sys.stderr)
        raise SystemExit(2)
    return httpx.Client(timeout=120.0)


def fetch_fund_instructions(
    client: HttpClient,
    base_url: str,
    *,
    package: str = "per_close",
) -> dict[str, Any]:
    resp = client.get(
        f"{base_url}/agents/crypto-test-payer/fund-instructions",
        params={"package": package},
    )
    resp.raise_for_status()
    return resp.json()


def suggested_deposit(amount_usd: float, *, buffer_usd: float = DEFAULT_BUFFER_USD) -> float:
    return round(float(amount_usd) + buffer_usd, 2)


def print_pay_instructions(
    checkout: dict[str, Any],
    *,
    package: str,
    network: str,
    buffer_usd: float,
) -> None:
    payment = checkout.get("payment") or {}
    pkg = checkout.get("package") or {}
    amount = float(payment.get("amount") or pkg.get("amount_usd") or 0)
    wallet = payment.get("wallet_address", "?")
    payment_id = payment.get("payment_id", "?")
    send_at_least = suggested_deposit(amount, buffer_usd=buffer_usd)

    print("=" * 72)
    print("Crypto Test Payer — Send USDC (you control when to pay)")
    print("=" * 72)
    print(f"  Package:         {pkg.get('name') or package} (${amount:.2f})")
    print(f"  Network:         {network}")
    print(f"  Send at least:   {send_at_least} USDC  (package + ${buffer_usd:.0f} buffer for refund)")
    print(f"  Wallet:          {wallet}")
    print(f"  Payment ID:      {payment_id}")
    if payment.get("memo"):
        print(f"  Memo:            {payment.get('memo')}")
    print("")
    print("  After your transfer confirms on-chain, run:")
    print(
        f"    python scripts/crypto_test_payer_agent.py run "
        f"--network {network} --package {package} --tx-hash <your_tx_hash>"
    )
    print("")
    print("  Or let the script auto-detect your deposit on Base:")
    print(
        f"    python scripts/crypto_test_payer_agent.py run "
        f"--network {network} --package {package}"
    )
    print("=" * 72)
    for step in (checkout.get("instructions") or {}).get("steps") or []:
        print(f"  • {step}")


def print_instructions(data: dict[str, Any], *, package: str) -> None:
    print("=" * 72)
    print("Crypto Test Payer — Overview")
    print("=" * 72)
    print(f"  Agent:           {data.get('agent')}")
    print(f"  Package:         {package} (${data.get('test_amount_usd')})")
    print(f"  Suggested send:  {data.get('suggested_deposit_usd')} USDC")
    print(f"  Receive wallet:  {data.get('receive_wallet')}")
    print(f"  Refund address:  {data.get('refund_address')}")
    print("")
    print("  Step 1 (no funds yet):")
    print(f"    python scripts/crypto_test_payer_agent.py checkout --network base --package {package}")
    print("  Step 2 (after you send USDC):")
    print(f"    python scripts/crypto_test_payer_agent.py run --network base --package {package}")
    print("=" * 72)


def register_sandbox(client: HttpClient, base_url: str) -> dict[str, Any]:
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
    return payload


def fetch_recent_usdc_inbound(
    wallet: str,
    *,
    since_iso: str | None = None,
    min_amount: float = 0.0,
) -> list[dict[str, Any]]:
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
        if amount < min_amount:
            continue
        inbound.append({
            "tx_hash": row.get("transaction_hash"),
            "amount": amount,
            "timestamp": ts,
            "from": (row.get("from") or {}).get("hash"),
        })
    return inbound


def create_checkout(
    client: HttpClient,
    base_url: str,
    *,
    package: str,
    network: str,
    partner_id: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "package": package,
        "network": network,
        "deal_id": "crypto_test_payer_001",
        "agent_id": "crypto_test_payer",
    }
    if partner_id:
        body["partner_id"] = partner_id
    resp = client.post(f"{base_url}/payments/crypto/checkout", json=body)
    resp.raise_for_status()
    return resp.json()


def poll_payment_status(
    client: HttpClient,
    base_url: str,
    payment_id: str,
    *,
    timeout_seconds: int = 120,
    interval_seconds: int = 5,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"{base_url}/payments/crypto/{payment_id}")
        resp.raise_for_status()
        payload = resp.json()
        payment = payload.get("payment") or payload
        last = payment
        status = str(payment.get("status", ""))
        if status == "confirmed" and resp.status_code == 200:
            return payment
        time.sleep(interval_seconds)
    raise RuntimeError(
        f"Payment {payment_id} not confirmed within {timeout_seconds}s "
        f"(last status: {last.get('status')})"
    )


def submit_tx_hash(
    client: HttpClient,
    base_url: str,
    payment_id: str,
    tx_hash: str,
) -> dict[str, Any]:
    resp = client.post(
        f"{base_url}/payments/crypto/{payment_id}/submit",
        json={"tx_hash": tx_hash},
    )
    resp.raise_for_status()
    return resp.json()


def operator_confirm(
    client: HttpClient,
    base_url: str,
    payment_id: str,
    tx_hash: str,
) -> dict[str, Any]:
    import confirm_crypto_payment as crypto_cli
    from arclya2a.server.operator_auth import load_operator_key

    operator_key = load_operator_key()
    if not operator_key or len(operator_key) < 8:
        raise RuntimeError("ARCLYA_OPERATOR_KEY required in .env for confirm step")
    return crypto_cli.confirm_payment_remote(
        client,
        base_url,
        payment_id,
        operator_key=operator_key,
        tx_hash=tx_hash,
        confirmed_by="crypto_test_payer",
    )


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


def run_checkout(
    *,
    network: str,
    package: str,
    buffer_usd: float = DEFAULT_BUFFER_USD,
) -> dict[str, Any]:
    """Create checkout and print instructions — no on-chain funds required."""
    base_url = resolve_base_url()
    with _shared_client() as client:
        state = load_state()
        checkout = create_checkout(
            client,
            base_url,
            package=package,
            network=network,
            partner_id=state.get("partner_id"),
        )
    payment = checkout.get("payment") or {}
    payment_id = payment.get("payment_id")
    if not payment_id:
        raise RuntimeError(f"Checkout missing payment_id: {checkout}")

    amount = float(payment.get("amount") or 0)
    state.update({
        "step": "awaiting_onchain_payment",
        "payment_id": payment_id,
        "checkout": checkout,
        "network": network,
        "package": package,
        "amount_usd": amount,
        "suggested_deposit_usd": suggested_deposit(amount, buffer_usd=buffer_usd),
        "buffer_usd": buffer_usd,
    })
    save_state(state)
    print_pay_instructions(checkout, package=package, network=network, buffer_usd=buffer_usd)
    return state


def resolve_funding_tx(
    *,
    wallet: str,
    min_amount: float,
    since_iso: str,
    tx_hash: str | None,
    network: str,
    wait_minutes: int,
) -> str:
    if tx_hash:
        return tx_hash.strip()
    if network != "base":
        raise RuntimeError("Auto deposit detection is Base-only; pass --tx-hash for other networks")
    print(f"Watching for inbound USDC >= {min_amount} to {wallet} (up to {wait_minutes} min) …")
    deadline = time.time() + wait_minutes * 60
    while time.time() < deadline:
        inbound = fetch_recent_usdc_inbound(wallet, since_iso=since_iso, min_amount=min_amount)
        if inbound:
            best = max(inbound, key=lambda r: r["amount"])
            print(f"  Found tx: {best['tx_hash']} ({best['amount']} USDC)")
            return str(best["tx_hash"])
        time.sleep(20)
    raise RuntimeError(
        f"No inbound USDC >= {min_amount} detected. Send USDC first, or re-run with --tx-hash <hash>"
    )


def run_test(
    *,
    network: str = "base",
    package: str = "per_close",
    tx_hash: str | None = None,
    wait_minutes: int = 30,
    skip_refund: bool = False,
    checkout_only: bool = False,
    buffer_usd: float = DEFAULT_BUFFER_USD,
    use_existing_checkout: bool = True,
) -> dict[str, Any]:
    base_url = resolve_base_url()
    state = load_state()

    if (
        use_existing_checkout
        and state.get("step") == "awaiting_onchain_payment"
        and state.get("payment_id")
        and state.get("package") == package
        and state.get("network") == network
        and not checkout_only
    ):
        checkout = state.get("checkout") or {}
        payment = checkout.get("payment") or {}
        payment_id = state["payment_id"]
        print(f"Resuming checkout payment_id={payment_id}")
    else:
        with _shared_client() as client:
            checkout = create_checkout(
                client,
                base_url,
                package=package,
                network=network,
                partner_id=state.get("partner_id"),
            )
        payment = checkout.get("payment") or {}
        payment_id = payment.get("payment_id")
        if not payment_id:
            raise RuntimeError(f"Checkout missing payment_id: {checkout}")
        amount = float(payment.get("amount") or 0)
        state.update({
            "step": "awaiting_onchain_payment",
            "payment_id": payment_id,
            "checkout": checkout,
            "network": network,
            "package": package,
            "amount_usd": amount,
            "suggested_deposit_usd": suggested_deposit(amount, buffer_usd=buffer_usd),
        })
        save_state(state)
        print_pay_instructions(checkout, package=package, network=network, buffer_usd=buffer_usd)

    if checkout_only:
        print("\nCheckout ready. No funds spent — send USDC when ready, then re-run without --checkout-only.")
        return state

    amount = float(state.get("amount_usd") or payment.get("amount") or 0)
    with _shared_client() as client:
        fund = fetch_fund_instructions(client, base_url, package=package)
    wallet = payment.get("wallet_address") or fund["receive_wallet"]
    created_at = payment.get("created_at") or state.get("updated_at") or _now_iso()
    refund_to = os.environ.get("ARCLYA_CRYPTO_TEST_PAYER_REFUND_ADDRESS", fund["refund_address"])

    resolved_tx = resolve_funding_tx(
        wallet=wallet,
        min_amount=amount,
        since_iso=created_at,
        tx_hash=tx_hash,
        network=network,
        wait_minutes=wait_minutes,
    )

    with _shared_client() as client:
        print("Submitting tx_hash …")
        submit_tx_hash(client, base_url, payment_id, resolved_tx)
        state["step"] = "submitted"
        state["tx_hash"] = resolved_tx
        save_state(state)

        print("Operator confirm …")
        confirm = operator_confirm(client, base_url, payment_id, resolved_tx)
        state["confirm"] = confirm
        save_state(state)

        print("Polling until payment confirmed …")
        confirmed = poll_payment_status(client, base_url, payment_id)
        state.update({"step": "confirmed", "payment": confirmed})
        save_state(state)
        print(f"  Confirmed: {payment_id} tx={resolved_tx}")

    deposit = float(state.get("suggested_deposit_usd") or suggested_deposit(amount, buffer_usd=buffer_usd))
    refund_amount = max(0.0, round(deposit - amount, 2))
    if skip_refund or refund_amount <= 0:
        print("Refund skipped.")
        return state

    if network != "base":
        print(f"Manual refund: send ${refund_amount:.2f} USDC to {refund_to} on {network}")
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
    parser = argparse.ArgumentParser(
        description="Arclya Crypto Test Payer — round-trip USDC checkout test",
    )
    parser.add_argument(
        "command",
        choices=["instructions", "register", "checkout", "run", "status", "refund"],
        help="checkout = dry run (no funds). run = full flow after you send USDC.",
    )
    parser.add_argument("--network", default="base", help="Network (default: base)")
    parser.add_argument(
        "--package",
        default="per_close",
        help="Package: per_close ($25), onboarding_package ($49), closer_access ($99)",
    )
    parser.add_argument("--tx-hash", help="On-chain tx hash (skip auto-detect)")
    parser.add_argument("--wait-minutes", type=int, default=30, help="Deposit watch timeout")
    parser.add_argument("--buffer-usd", type=float, default=DEFAULT_BUFFER_USD, help="Extra USDC for refund")
    parser.add_argument("--amount", type=float, help="Refund amount (refund command)")
    parser.add_argument("--skip-refund", action="store_true", help="Skip surplus refund")
    parser.add_argument(
        "--checkout-only",
        action="store_true",
        help="With run: stop after creating checkout (same as checkout command)",
    )
    parser.add_argument(
        "--fresh-checkout",
        action="store_true",
        help="Ignore saved checkout state and create a new payment intent",
    )
    args = parser.parse_args(argv)

    base_url = resolve_base_url()

    if args.command == "instructions":
        with _shared_client() as client:
            print_instructions(fetch_fund_instructions(client, base_url, package=args.package), package=args.package)
        return 0
    if args.command == "register":
        with _shared_client() as client:
            register_sandbox(client, base_url)
        return 0
    if args.command == "status":
        print(json.dumps(load_state(), indent=2))
        return 0
    if args.command == "checkout":
        run_checkout(network=args.network, package=args.package, buffer_usd=args.buffer_usd)
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
            checkout_only=args.checkout_only,
            buffer_usd=args.buffer_usd,
            use_existing_checkout=not args.fresh_checkout,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())