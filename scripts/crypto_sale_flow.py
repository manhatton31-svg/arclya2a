"""Remote-driven first crypto sale: intent → send USDC → submit → operator confirm.

Persists progress to data/ops/crypto_sale_flow.json so Grok Build can resume steps.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "ops" / "crypto_sale_flow.json"

DEFAULT_PARTNER_ID = "tp_e59937ce24ac"
DEFAULT_NETWORK = "base"
DEFAULT_AMOUNT_USD = 1.0
DEFAULT_DEAL_ID = "first_crypto_sale_001"


class HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> Any: ...
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...


def resolve_base_url() -> str:
    return os.environ.get("ARCLYA_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"step": "not_started", "updated_at": None}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"step": "not_started", "updated_at": None, "error": "corrupt_state_file"}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _auth_headers(production_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = (production_key or os.environ.get("ARCLYA_PRODUCTION_KEY") or "").strip()
    if key:
        headers["X-Arclya-Key"] = key
    return headers


def _extract_payment(payload: dict[str, Any]) -> dict[str, Any]:
    if "payment" in payload and isinstance(payload["payment"], dict):
        return payload["payment"]
    return payload


def create_payment_intent(
    client: HttpClient,
    base_url: str,
    *,
    partner_id: str,
    amount_usd: float,
    network: str = DEFAULT_NETWORK,
    deal_id: str | None = None,
    production_key: str | None = None,
    memo: str | None = None,
) -> dict[str, Any]:
    """POST /payments/crypto/intent on the live server."""
    body: dict[str, Any] = {
        "amount": amount_usd,
        "network": network,
        "partner_id": partner_id,
        "deal_id": deal_id or DEFAULT_DEAL_ID,
    }
    if memo:
        body["memo"] = memo
    resp = client.post(
        f"{base_url.rstrip('/')}/payments/crypto/intent",
        json=body,
        headers=_auth_headers(production_key),
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": getattr(resp, "text", "")}
    if resp.status_code not in (200, 201, 402):
        message = "unknown error"
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            message = err.get("message", message) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Intent failed (HTTP {resp.status_code}): {message}")
    payment = _extract_payment(payload)
    return {"ok": True, "payment": payment, "response": payload}


def format_send_instructions(
    payment: dict[str, Any],
    *,
    base_url: str,
    partner_id: str | None = None,
) -> str:
    """Human-readable USDC send instructions for the operator."""
    amount = payment.get("amount") or payment.get("amount_usd")
    currency = payment.get("currency", "USDC")
    network = payment.get("network", DEFAULT_NETWORK)
    wallet = payment.get("wallet_address", "?")
    payment_id = payment.get("payment_id", "?")
    memo = payment.get("memo") or ""
    explorer = {
        "base": "https://basescan.org/address/",
        "ethereum": "https://etherscan.io/address/",
        "bnb": "https://bscscan.com/address/",
        "solana": "https://solscan.io/account/",
    }.get(str(network).lower(), "")

    lines = [
        "=" * 72,
        "Send USDC — First Crypto Sale",
        "=" * 72,
        f"  Payment ID:     {payment_id}",
        f"  Partner ID:     {partner_id or payment.get('partner_id', '-')}",
        f"  Amount:         {amount} {currency}  (send exactly this amount)",
        f"  Network:        {network}",
        f"  Wallet:         {wallet}",
    ]
    if memo:
        lines.append(f"  Memo (if supported): {memo}")
    if explorer:
        lines.append(f"  Explorer:       {explorer}{wallet}")
    lines.extend([
        "",
        "Steps:",
        f"  1. Open your wallet on {network.upper()}",
        f"  2. Send exactly {amount} USDC to the wallet above",
        "  3. Copy the transaction hash (tx_hash) after it confirms",
        "  4. Tell Grok Build the tx_hash, or run:",
        f"     python scripts/run_first_crypto_sale.py sale submit --tx-hash <tx_hash>",
        "",
        f"  Status URL:   {base_url}/payments/crypto/{payment_id}",
        "=" * 72,
    ])
    return "\n".join(lines)


def submit_tx_hash(
    client: HttpClient,
    base_url: str,
    payment_id: str,
    tx_hash: str,
    *,
    production_key: str | None = None,
) -> dict[str, Any]:
    resp = client.post(
        f"{base_url.rstrip('/')}/payments/crypto/{payment_id}/submit",
        json={"tx_hash": tx_hash.strip()},
        headers=_auth_headers(production_key),
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": getattr(resp, "text", "")}
    if resp.status_code not in (200, 201, 402):
        message = "unknown error"
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            message = err.get("message", message) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Submit failed (HTTP {resp.status_code}): {message}")
    payment = _extract_payment(payload)
    return {"ok": True, "payment": payment, "response": payload}


def fetch_payment_status(
    client: HttpClient,
    base_url: str,
    payment_id: str,
) -> dict[str, Any]:
    resp = client.get(f"{base_url.rstrip('/')}/payments/crypto/{payment_id}")
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": getattr(resp, "text", "")}
    payment = _extract_payment(payload) if isinstance(payload, dict) else {}
    return {
        "ok": resp.status_code in (200, 402),
        "status_code": resp.status_code,
        "payment": payment,
        "response": payload,
    }


def run_sale_start(
    client: HttpClient,
    *,
    base_url: str | None = None,
    partner_id: str | None = None,
    amount_usd: float | None = None,
    network: str = DEFAULT_NETWORK,
    deal_id: str | None = None,
    production_key: str | None = None,
) -> dict[str, Any]:
    """Create payment intent and persist state."""
    base = (base_url or resolve_base_url()).rstrip("/")
    partner = partner_id or os.environ.get("ARCLYA_PARTNER_ID", DEFAULT_PARTNER_ID)
    amount = amount_usd if amount_usd is not None else float(
        os.environ.get("ARCLYA_SALE_AMOUNT_USD", DEFAULT_AMOUNT_USD)
    )
    deal = deal_id or os.environ.get("ARCLYA_DEAL_ID", DEFAULT_DEAL_ID)

    result = create_payment_intent(
        client,
        base,
        partner_id=partner,
        amount_usd=amount,
        network=network,
        deal_id=deal,
        production_key=production_key,
        memo=f"Arclya sale {deal}",
    )
    payment = result["payment"]
    state = {
        "step": "awaiting_onchain_payment",
        "base_url": base,
        "partner_id": partner,
        "deal_id": deal,
        "amount_usd": amount,
        "network": network,
        "payment_id": payment.get("payment_id"),
        "wallet_address": payment.get("wallet_address"),
        "memo": payment.get("memo"),
        "payment": payment,
    }
    save_state(state)
    print(format_send_instructions(payment, base_url=base, partner_id=partner))
    return {"ok": True, "state": state, **result}


def run_sale_submit(
    client: HttpClient,
    tx_hash: str,
    *,
    base_url: str | None = None,
    payment_id: str | None = None,
    production_key: str | None = None,
) -> dict[str, Any]:
    """Submit on-chain tx_hash for the active sale."""
    state = load_state()
    base = (base_url or state.get("base_url") or resolve_base_url()).rstrip("/")
    pid = payment_id or state.get("payment_id")
    if not pid:
        raise RuntimeError("No payment_id in state. Run: sale start")

    result = submit_tx_hash(client, base, pid, tx_hash, production_key=production_key)
    payment = result["payment"]
    state.update({
        "step": "awaiting_operator_confirm",
        "tx_hash": tx_hash.strip(),
        "payment_id": pid,
        "payment": payment,
    })
    save_state(state)
    print("=" * 72)
    print("Tx hash submitted")
    print("=" * 72)
    print(f"  Payment ID:  {pid}")
    print(f"  Status:      {payment.get('status', 'submitted')}")
    print(f"  Tx hash:     {tx_hash.strip()}")
    print("")
    print("  Next: operator confirm (remote):")
    print(f"    python scripts/run_first_crypto_sale.py sale confirm")
    print("=" * 72)
    return {"ok": True, "state": state, **result}


def run_sale_confirm(
    client: HttpClient,
    *,
    base_url: str | None = None,
    payment_id: str | None = None,
    tx_hash: str | None = None,
) -> dict[str, Any]:
    """Operator-confirm payment after on-chain verification."""
    from arclya2a.server.operator_auth import load_operator_key
    import confirm_crypto_payment as crypto_cli

    state = load_state()
    base = (base_url or state.get("base_url") or resolve_base_url()).rstrip("/")
    pid = payment_id or state.get("payment_id")
    if not pid:
        raise RuntimeError("No payment_id in state. Run: sale start")

    operator_key = load_operator_key()
    if not operator_key or len(operator_key) < 8:
        raise RuntimeError("ARCLYA_OPERATOR_KEY required (min 8 chars)")

    resolved_tx = tx_hash or state.get("tx_hash")
    who = os.environ.get("ARCLYA_OPERATOR_ID", "crypto_sale_flow")
    result = crypto_cli.confirm_payment_remote(
        client,
        base,
        pid,
        operator_key=operator_key,
        tx_hash=resolved_tx,
        confirmed_by=who,
    )
    print(crypto_cli.format_confirm_result(pid, result))
    payment = result.get("payment") or {}
    state.update({
        "step": "confirmed",
        "payment_id": pid,
        "tx_hash": payment.get("tx_hash") or resolved_tx,
        "payment": payment,
    })
    save_state(state)
    return {"ok": True, "state": state, "result": result}


def run_sale_status(
    client: HttpClient,
    *,
    base_url: str | None = None,
    payment_id: str | None = None,
) -> dict[str, Any]:
    """Fetch current payment status."""
    state = load_state()
    base = (base_url or state.get("base_url") or resolve_base_url()).rstrip("/")
    pid = payment_id or state.get("payment_id")
    if not pid:
        return {"ok": False, "error": "no_payment_id", "state": state}

    result = fetch_payment_status(client, base, pid)
    payment = result.get("payment") or {}
    print("=" * 72)
    print("Crypto Sale Status")
    print("=" * 72)
    print(f"  Flow step:     {state.get('step', '?')}")
    print(f"  Payment ID:    {pid}")
    print(f"  HTTP:          {result.get('status_code')}")
    print(f"  Status:        {payment.get('status', '?')}")
    print(f"  Amount:        {payment.get('amount', '?')} {payment.get('currency', 'USDC')}")
    print(f"  Network:       {payment.get('network', '?')}")
    print(f"  Tx hash:       {payment.get('tx_hash') or state.get('tx_hash') or '-'}")
    print("=" * 72)
    return {"ok": True, "state": state, **result}


def run_sale_next() -> dict[str, Any]:
    """Print the next remote action for Grok Build or the operator."""
    state = load_state()
    step = state.get("step", "not_started")
    guides = {
        "not_started": (
            "Run sale start to create a $1 USDC intent on Base:\n"
            "  python scripts/run_first_crypto_sale.py sale start "
            "--partner-id tp_e59937ce24ac --amount 1 --network base"
        ),
        "awaiting_onchain_payment": (
            "Send USDC per saved instructions, then:\n"
            "  python scripts/run_first_crypto_sale.py sale submit --tx-hash <hash>\n"
            f"  Wallet: {state.get('wallet_address', '?')}  "
            f"Amount: {state.get('amount_usd', '?')} on {state.get('network', 'base')}"
        ),
        "awaiting_operator_confirm": (
            "Operator confirm after verifying on-chain:\n"
            "  python scripts/run_first_crypto_sale.py sale confirm\n"
            f"  payment_id={state.get('payment_id')} tx_hash={state.get('tx_hash', '?')}"
        ),
        "confirmed": (
            "Sale complete. Verify:\n"
            f"  python scripts/run_first_crypto_sale.py verify "
            f"--payment-id {state.get('payment_id')} "
            f"--partner-id {state.get('partner_id')} "
            f"--deal-id {state.get('deal_id')}"
        ),
    }
    message = guides.get(step, f"Unknown step: {step}")
    print("=" * 72)
    print("Next step — Crypto Sale Flow")
    print("=" * 72)
    print(message)
    print("=" * 72)
    return {"ok": True, "step": step, "state": state, "next": message}