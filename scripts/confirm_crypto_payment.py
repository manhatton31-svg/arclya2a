#!/usr/bin/env python3
"""Operator CLI: list pending crypto payments and confirm after on-chain verification."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.server.operator_auth import load_operator_key


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


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def format_age(created_at: str | None) -> str:
    dt = _parse_ts(created_at)
    if not dt:
        return "unknown"
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        minutes = max(1, int(delta.total_seconds() // 60))
        return f"{minutes}m"
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def resolve_agent_label(root: Path, partner_id: str | None, metadata: dict[str, Any] | None) -> str:
    if metadata and metadata.get("agent_id"):
        return str(metadata["agent_id"])
    if not partner_id:
        return "-"
    try:
        from arclya2a.partners.test_registry import get_test_partner

        partner = get_test_partner(root, partner_id)
        if partner and partner.get("agent_name"):
            return str(partner["agent_name"])
    except Exception:
        pass
    return partner_id


def enrich_pending_rows(root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["agent_label"] = resolve_agent_label(
            root,
            row.get("partner_id"),
            row.get("metadata") if isinstance(row.get("metadata"), dict) else None,
        )
        item["age"] = format_age(row.get("created_at"))
        enriched.append(item)
    return enriched


def fetch_pending_review(client: HttpClient, base_url: str) -> dict[str, Any]:
    """Load crypto payment review queue from ops dashboard."""
    resp = client.get(f"{base_url}/ops/dashboard")
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to load dashboard: HTTP {resp.status_code}")
    data = resp.json()
    payments = data.get("payments") or {}
    pending = list(payments.get("pending_review") or [])
    by_status = payments.get("by_status") or {}
    return {
        "pending_review": pending,
        "pending_review_count": payments.get("pending_review_count", len(pending)),
        "by_status": by_status,
        "confirmed_total_usd": payments.get("confirmed_total_usd", 0),
    }


def format_pending_list(summary: dict[str, Any], *, enriched: list[dict[str, Any]] | None = None) -> str:
    rows = enriched if enriched is not None else summary.get("pending_review") or []
    count = summary.get("pending_review_count", len(rows))
    by_status = summary.get("by_status") or {}
    lines = [
        "=" * 72,
        "Arclya Crypto Payments — Needs Review",
        "=" * 72,
        f"  Needs review:  {count}",
        f"  Pending:       {by_status.get('pending', 0)}",
        f"  Submitted:     {by_status.get('submitted', 0)}",
        f"  Confirmed:     {by_status.get('confirmed', 0)}",
        f"  Failed:        {by_status.get('failed', 0)}",
        f"  Confirmed USD: ${summary.get('confirmed_total_usd', 0):.2f}",
        "",
    ]
    if not rows:
        lines.append("No payments need review.")
    else:
        lines.append(
            f"{'payment_id':18} {'partner/agent':22} {'amount':>10} {'network':8} {'status':10} age"
        )
        lines.append("-" * 72)
        for row in rows:
            partner = row.get("partner_id") or "-"
            agent = row.get("agent_label") or partner
            label = agent if agent == partner else f"{partner}/{agent}"
            amount = row.get("amount", 0)
            currency = row.get("currency", "USDC")
            lines.append(
                f"{row.get('payment_id', '?'):18} "
                f"{label[:22]:22} "
                f"${float(amount):>8.2f} {currency[:3]:3} "
                f"{row.get('network', '?'):8} "
                f"{row.get('status', '?'):10} "
                f"{row.get('age', format_age(row.get('created_at')))}"
            )
            tx = row.get("tx_hash")
            if tx:
                lines.append(f"    tx: {tx}")
    lines.append("=" * 72)
    lines.append("Confirm: python scripts/confirm_crypto_payment.py --confirm <payment_id>")
    return "\n".join(lines)


def confirm_payment_remote(
    client: HttpClient,
    base_url: str,
    payment_id: str,
    *,
    operator_key: str,
    tx_hash: str | None = None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    headers = {"X-Arclya-Operator-Key": operator_key}
    if confirmed_by:
        headers["X-Arclya-Operator-Id"] = confirmed_by
    body: dict[str, Any] = {}
    if tx_hash:
        body["tx_hash"] = tx_hash
    if confirmed_by:
        body["confirmed_by"] = confirmed_by
    resp = client.post(
        f"{base_url}/payments/crypto/{payment_id}/confirm",
        json=body or None,
        headers=headers,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text if hasattr(resp, "text") else str(resp)}
    if resp.status_code not in (200, 201):
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else str(payload)
        raise RuntimeError(f"Confirm failed (HTTP {resp.status_code}): {message}")
    return payload


def format_confirm_result(payment_id: str, result: dict[str, Any]) -> str:
    payment = result.get("payment") or {}
    duplicate = result.get("duplicate", False)
    lines = [
        "=" * 72,
        "Crypto Payment Confirmed" if not duplicate else "Crypto Payment Already Confirmed",
        "=" * 72,
        f"  Payment ID:    {payment_id}",
        f"  Status:        {payment.get('status', result.get('status', '?'))}",
        f"  Amount:        ${payment.get('amount', 0)} {payment.get('currency', 'USDC')}",
        f"  Network:       {payment.get('network', '?')}",
        f"  Tx hash:       {payment.get('tx_hash') or '-'}",
        f"  Confirmed at:  {payment.get('confirmed_at') or '-'}",
    ]
    if duplicate:
        lines.append("  Note:          Payment was already confirmed (no changes made).")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, http_client: HttpClient | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List pending crypto payments or confirm after on-chain verification",
    )
    parser.add_argument(
        "--confirm",
        dest="payment_id",
        metavar="PAYMENT_ID",
        help="Confirm a payment (e.g. cpay_abc123)",
    )
    parser.add_argument("--tx-hash", dest="tx_hash", help="Verified on-chain transaction hash")
    parser.add_argument(
        "--confirmed-by",
        default=os.environ.get("ARCLYA_OPERATOR_ID", "operator_cli"),
        help="Operator name for audit log",
    )
    parser.add_argument(
        "--base-url",
        default=resolve_base_url(),
        help="Arclya server URL (default: ARCLYA_BASE_URL or http://127.0.0.1:8787)",
    )
    parser.add_argument(
        "--operator-key",
        default=os.environ.get("ARCLYA_OPERATOR_KEY", "").strip() or None,
        help="Operator key (default: ARCLYA_OPERATOR_KEY env)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    operator_key = args.operator_key or load_operator_key()
    if args.payment_id and (not operator_key or len(operator_key) < 8):
        print(
            "ERROR: Operator key required for confirm (min 8 chars). Set ARCLYA_OPERATOR_KEY.",
            file=sys.stderr,
        )
        return 2

    base_url = args.base_url.rstrip("/")
    http = None
    if http_client is not None:
        client = http_client
    else:
        try:
            import httpx

            http = httpx.Client(timeout=30.0)
        except ImportError:
            print("ERROR: httpx is required. Install with: pip install httpx", file=sys.stderr)
            return 2

        class _HttpxAdapter:
            def get(self, url: str, *, headers: dict[str, str] | None = None):
                return http.get(url, headers=headers)

            def post(self, url: str, *, json=None, headers: dict[str, str] | None = None):
                return http.post(url, json=json, headers=headers)

        client = _HttpxAdapter()

    try:
        if args.payment_id:
            result = confirm_payment_remote(
                client,
                base_url,
                args.payment_id,
                operator_key=operator_key,
                tx_hash=args.tx_hash,
                confirmed_by=args.confirmed_by,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(format_confirm_result(args.payment_id, result))
            return 0

        summary = fetch_pending_review(client, base_url)
        enriched = enrich_pending_rows(ROOT, summary.get("pending_review") or [])
        if args.json:
            print(json.dumps({**summary, "pending_review": enriched}, indent=2, default=str))
        else:
            print(format_pending_list(summary, enriched=enriched))
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if http is not None:
            http.close()


if __name__ == "__main__":
    raise SystemExit(main())