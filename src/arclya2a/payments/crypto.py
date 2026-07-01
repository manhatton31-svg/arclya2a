"""Crypto payment tracking — intents, records, status, and operator-confirmed settlement."""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.settings import (
    SUPPORTED_CRYPTO_NETWORKS,
    CryptoPaymentSettings,
    get_settings,
    normalize_crypto_network,
)

# Payment / intent lifecycle statuses
STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_CONFIRMED = "confirmed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

VALID_PAYMENT_STATUSES = frozenset({
    STATUS_PENDING,
    STATUS_SUBMITTED,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
})

# Backward-compatible aliases
INTENT_STATUS_PENDING = STATUS_PENDING
INTENT_STATUS_SUBMITTED = STATUS_SUBMITTED
INTENT_STATUS_CONFIRMED = STATUS_CONFIRMED
INTENT_STATUS_EXPIRED = STATUS_EXPIRED
INTENT_STATUS_CANCELLED = STATUS_CANCELLED
VALID_INTENT_STATUSES = VALID_PAYMENT_STATUSES - {STATUS_FAILED}


@dataclass
class CryptoPayment:
    """Canonical crypto payment record for agent/partner sales tracking."""

    payment_id: str
    amount: float
    currency: str
    network: str
    wallet_address: str
    status: str = STATUS_PENDING
    partner_id: str | None = None
    deal_id: str | None = None
    intent_id: str | None = None
    customer_ref: str | None = None
    agent_id: str | None = None
    memo: str | None = None
    tx_hash: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None
    confirmed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """Checkout-safe view for payers."""
        return {
            "payment_id": self.payment_id,
            "intent_id": self.intent_id,
            "amount": self.amount,
            "currency": self.currency,
            "network": self.network,
            "wallet_address": self.wallet_address,
            "status": self.status,
            "memo": self.memo,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "tx_hash": self.tx_hash,
        }


@dataclass
class CryptoPaymentIntent:
    """A request for crypto payment before on-chain confirmation."""

    intent_id: str
    amount_usd: float
    token: str
    network: str
    wallet_address: str
    status: str = STATUS_PENDING
    payment_id: str | None = None
    partner_id: str | None = None
    deal_id: str | None = None
    customer_ref: str | None = None
    memo: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None
    submitted_tx_hash: str | None = None
    confirmed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_checkout_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "payment_id": self.payment_id,
            "amount_usd": self.amount_usd,
            "token": self.token,
            "network": self.network,
            "wallet_address": self.wallet_address,
            "status": self.status,
            "memo": self.memo,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


def _payments_path(root: Path) -> Path:
    path = root / "data" / "payments" / "crypto_payments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _intents_path(root: Path) -> Path:
    path = root / "data" / "payments" / "crypto_intents.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _crypto_settings() -> CryptoPaymentSettings:
    return get_settings().crypto


def is_crypto_payments_enabled() -> bool:
    return _crypto_settings().enabled


def is_crypto_payments_configured() -> bool:
    return _crypto_settings().configured


def list_accepted_crypto_networks() -> list[dict[str, Any]]:
    """Return checkout-ready network options (USDC receive addresses)."""
    cfg = _crypto_settings()
    options: list[dict[str, Any]] = []
    for network in cfg.networks:
        address = cfg.wallets.get(network)
        if not address:
            continue
        options.append({
            "network": network,
            "token": cfg.token,
            "wallet_address": address,
        })
    return options


def _wallet_env_hint(network: str) -> str:
    return f"ARCLYA_CRYPTO_WALLET_{network.upper()}"


def _resolve_network(cfg: CryptoPaymentSettings, network: str | None) -> str:
    if network:
        canonical = normalize_crypto_network(network)
        if not canonical:
            supported = ", ".join(sorted(SUPPORTED_CRYPTO_NETWORKS))
            raise ValueError(f"Unsupported network '{network}'. Supported: {supported}")
        if not cfg.accepts_network(canonical):
            accepted = ", ".join(cfg.networks) or "none"
            raise ValueError(
                f"Network '{canonical}' is not configured. "
                f"Set {_wallet_env_hint(canonical)}. Accepted: {accepted}"
            )
        return canonical
    if not cfg.default_network or not cfg.accepts_network(cfg.default_network):
        raise ValueError("No default crypto network configured.")
    return cfg.default_network


def _validate_payment_enabled(cfg: CryptoPaymentSettings, amount: float) -> None:
    if not cfg.configured:
        raise ValueError(
            "Crypto payments are not configured. Set per-network wallets, e.g. "
            "ARCLYA_CRYPTO_WALLET_BASE, ARCLYA_CRYPTO_WALLET_ETHEREUM, "
            "ARCLYA_CRYPTO_WALLET_SOLANA, ARCLYA_CRYPTO_WALLET_BNB "
            f"(networks: {', '.join(sorted(SUPPORTED_CRYPTO_NETWORKS))})."
        )
    if not cfg.enabled:
        raise ValueError(
            "Crypto payments are disabled. Set ARCLYA_CRYPTO_ENABLED=1 to accept payments."
        )
    if amount < cfg.min_amount_usd:
        raise ValueError(
            f"Amount ${amount:.2f} is below minimum ${cfg.min_amount_usd:.2f}"
        )


def _generate_memo(record_id: str) -> str:
    suffix = secrets.token_hex(3)
    return f"arclya-{record_id[:8]}-{suffix}"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _latest_by_key(
    rows: list[dict[str, Any]],
    key: str,
    *,
    sort_field: str = "updated_at",
) -> dict[str, dict[str, Any]]:
    """Collapse append-only snapshots to latest row per key."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = row.get(key)
        if not row_key:
            continue
        existing = latest.get(row_key)
        row_ts = row.get(sort_field) or row.get("created_at", "")
        if not existing:
            latest[row_key] = row
            continue
        existing_ts = existing.get(sort_field) or existing.get("created_at", "")
        if row_ts >= existing_ts:
            latest[row_key] = row
    return latest


def _append_payment(root: Path, payment: CryptoPayment) -> CryptoPayment:
    _append_jsonl(_payments_path(root), payment.to_dict())
    return payment


def record_crypto_payment(
    root: Path,
    *,
    amount: float,
    network: str | None = None,
    partner_id: str | None = None,
    deal_id: str | None = None,
    agent_id: str | None = None,
    customer_ref: str | None = None,
    memo: str | None = None,
    intent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CryptoPayment:
    """Record a new crypto payment in pending status."""
    cfg = _crypto_settings()
    _validate_payment_enabled(cfg, amount)
    resolved_network = _resolve_network(cfg, network)
    wallet_address = cfg.wallet_for(resolved_network) or ""
    now = datetime.now(timezone.utc).isoformat()
    payment_id = f"cpay_{uuid.uuid4().hex[:12]}"
    expires = datetime.now(timezone.utc) + timedelta(hours=cfg.intent_expiry_hours)
    payment = CryptoPayment(
        payment_id=payment_id,
        amount=round(float(amount), 2),
        currency=cfg.token,
        network=resolved_network,
        wallet_address=wallet_address,
        status=STATUS_PENDING,
        partner_id=partner_id,
        deal_id=deal_id,
        agent_id=agent_id,
        customer_ref=customer_ref,
        intent_id=intent_id,
        memo=memo or _generate_memo(payment_id),
        expires_at=expires.isoformat(),
        created_at=now,
        updated_at=now,
        metadata=metadata or {},
    )
    return _append_payment(root, payment)


def update_crypto_payment(
    root: Path,
    payment_id: str,
    *,
    status: str,
    tx_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an updated payment snapshot (append-only store)."""
    if status not in VALID_PAYMENT_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    existing = get_crypto_payment(root, payment_id)
    if not existing:
        raise KeyError(f"Payment not found: {payment_id}")

    now = datetime.now(timezone.utc).isoformat()
    updated = dict(existing)
    updated["status"] = status
    updated["updated_at"] = now
    if tx_hash:
        updated["tx_hash"] = tx_hash
    if status == STATUS_CONFIRMED:
        updated["confirmed_at"] = now
    if status == STATUS_FAILED:
        updated["confirmed_at"] = None
    if metadata:
        merged = dict(updated.get("metadata") or {})
        merged.update(metadata)
        updated["metadata"] = merged

    _append_jsonl(_payments_path(root), updated)
    _sync_intent_from_payment(root, updated)
    return updated


def get_crypto_payment(root: Path, payment_id: str) -> dict[str, Any] | None:
    rows = _read_jsonl(_payments_path(root))
    return _latest_by_key(rows, "payment_id").get(payment_id)


def list_crypto_payments(
    root: Path,
    *,
    status: str | None = None,
    partner_id: str | None = None,
    deal_id: str | None = None,
    network: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List latest crypto payment records, newest first."""
    rows = list(_latest_by_key(_read_jsonl(_payments_path(root)), "payment_id").values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if partner_id:
        rows = [r for r in rows if r.get("partner_id") == partner_id]
    if deal_id:
        rows = [r for r in rows if r.get("deal_id") == deal_id]
    if network:
        canonical = normalize_crypto_network(network)
        if canonical:
            rows = [r for r in rows if r.get("network") == canonical]
    rows.sort(key=lambda r: r.get("updated_at", r.get("created_at", "")), reverse=True)
    return rows[: max(1, limit)]


def get_crypto_payments_by_partner(root: Path, partner_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return list_crypto_payments(root, partner_id=partner_id, limit=limit)


def get_crypto_payments_by_deal(root: Path, deal_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return list_crypto_payments(root, deal_id=deal_id, limit=limit)


def create_crypto_payment_intent(
    root: Path,
    *,
    amount_usd: float,
    network: str | None = None,
    partner_id: str | None = None,
    deal_id: str | None = None,
    customer_ref: str | None = None,
    memo: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CryptoPaymentIntent:
    """Create a payment intent and linked payment record."""
    cfg = _crypto_settings()
    _validate_payment_enabled(cfg, amount_usd)
    resolved_network = _resolve_network(cfg, network)
    wallet_address = cfg.wallet_for(resolved_network) or ""

    payment_id = f"cpay_{uuid.uuid4().hex[:12]}"
    intent_id = f"cpi_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    expires = datetime.now(timezone.utc) + timedelta(hours=cfg.intent_expiry_hours)
    shared_memo = memo or _generate_memo(payment_id)

    payment = CryptoPayment(
        payment_id=payment_id,
        amount=round(float(amount_usd), 2),
        currency=cfg.token,
        network=resolved_network,
        wallet_address=wallet_address,
        status=STATUS_PENDING,
        partner_id=partner_id,
        deal_id=deal_id,
        customer_ref=customer_ref,
        intent_id=intent_id,
        memo=shared_memo,
        expires_at=expires.isoformat(),
        created_at=now,
        updated_at=now,
        metadata=metadata or {},
    )
    _append_payment(root, payment)

    intent = CryptoPaymentIntent(
        intent_id=intent_id,
        payment_id=payment_id,
        amount_usd=payment.amount,
        token=payment.currency,
        network=payment.network,
        wallet_address=payment.wallet_address,
        status=STATUS_PENDING,
        partner_id=partner_id,
        deal_id=deal_id,
        customer_ref=customer_ref,
        memo=shared_memo,
        expires_at=payment.expires_at,
        created_at=now,
        metadata=metadata or {},
    )
    _append_jsonl(_intents_path(root), intent.to_dict())
    return intent


def list_crypto_payment_intents(
    root: Path,
    *,
    status: str | None = None,
    partner_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = list(_latest_by_key(_read_jsonl(_intents_path(root)), "intent_id").values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if partner_id:
        rows = [r for r in rows if r.get("partner_id") == partner_id]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows[: max(1, limit)]


def get_crypto_payment_intent(root: Path, intent_id: str) -> dict[str, Any] | None:
    rows = _read_jsonl(_intents_path(root))
    return _latest_by_key(rows, "intent_id").get(intent_id)


def _rewrite_intent(root: Path, updated: dict[str, Any]) -> None:
    path = _intents_path(root)
    intent_id = updated.get("intent_id")
    if not intent_id:
        return
    _append_jsonl(path, updated)


def _sync_intent_from_payment(root: Path, payment: dict[str, Any]) -> None:
    intent_id = payment.get("intent_id")
    if not intent_id:
        return
    intent = get_crypto_payment_intent(root, intent_id)
    if not intent:
        return
    intent = dict(intent)
    intent["status"] = payment.get("status", intent.get("status"))
    intent["submitted_tx_hash"] = payment.get("tx_hash")
    if payment.get("status") == STATUS_CONFIRMED:
        intent["confirmed_at"] = payment.get("confirmed_at")
    _rewrite_intent(root, intent)


def update_crypto_payment_intent(
    root: Path,
    intent_id: str,
    *,
    status: str,
    submitted_tx_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update intent status and sync linked payment record."""
    if status not in VALID_INTENT_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    existing = get_crypto_payment_intent(root, intent_id)
    if not existing:
        raise KeyError(f"Payment intent not found: {intent_id}")

    now = datetime.now(timezone.utc).isoformat()
    updated_intent = dict(existing)
    updated_intent["status"] = status
    if submitted_tx_hash:
        updated_intent["submitted_tx_hash"] = submitted_tx_hash
    if status == STATUS_CONFIRMED:
        updated_intent["confirmed_at"] = now
    if metadata:
        merged = dict(updated_intent.get("metadata") or {})
        merged.update(metadata)
        updated_intent["metadata"] = merged

    _rewrite_intent(root, updated_intent)

    payment_id = existing.get("payment_id")
    if payment_id:
        return update_crypto_payment(
            root,
            payment_id,
            status=status,
            tx_hash=submitted_tx_hash,
            metadata=metadata,
        )

    return updated_intent


def confirm_crypto_payment(
    root: Path,
    payment_id: str,
    *,
    confirmed_by: str,
    tx_hash: str | None = None,
) -> dict[str, Any]:
    """Operator-confirmed on-chain payment (manual verification workflow)."""
    existing = get_crypto_payment(root, payment_id)
    if not existing:
        raise KeyError(f"Payment not found: {payment_id}")

    if existing.get("status") == STATUS_CONFIRMED:
        return {**existing, "duplicate": True}

    resolved_tx = (tx_hash or existing.get("tx_hash") or "").strip() or None
    updated = update_crypto_payment(
        root,
        payment_id,
        status=STATUS_CONFIRMED,
        tx_hash=resolved_tx,
        metadata={
            "confirmed_by": confirmed_by,
            "confirmed_via": "operator",
        },
    )

    audit = append_audit_record(
        root,
        agent_id="operator",
        action="crypto_payment_confirmed",
        reasoning=f"Confirmed crypto payment {payment_id}",
        metadata={
            "category": "crypto_payment",
            "payment_id": payment_id,
            "intent_id": updated.get("intent_id"),
            "partner_id": updated.get("partner_id"),
            "deal_id": updated.get("deal_id"),
            "amount": updated.get("amount"),
            "currency": updated.get("currency"),
            "network": updated.get("network"),
            "tx_hash": updated.get("tx_hash"),
            "confirmed_by": confirmed_by,
        },
    )
    return {**updated, "audit_id": audit["id"], "duplicate": False}


def crypto_payments_summary(root: Path) -> dict[str, Any]:
    """Aggregate crypto payment stats for ops dashboards."""
    cfg = _crypto_settings()
    payments = list_crypto_payments(root, limit=500)
    intents = list_crypto_payment_intents(root, limit=500)

    by_status: dict[str, int] = {
        STATUS_PENDING: 0,
        STATUS_SUBMITTED: 0,
        STATUS_CONFIRMED: 0,
        STATUS_FAILED: 0,
        STATUS_EXPIRED: 0,
        STATUS_CANCELLED: 0,
    }
    confirmed_usd = 0.0
    for row in payments:
        st = str(row.get("status", "unknown"))
        by_status[st] = by_status.get(st, 0) + 1
        if st == STATUS_CONFIRMED:
            confirmed_usd += float(row.get("amount", row.get("amount_usd", 0)))

    def _review_row(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "payment_id": p.get("payment_id"),
            "status": p.get("status"),
            "amount": p.get("amount"),
            "currency": p.get("currency"),
            "network": p.get("network"),
            "partner_id": p.get("partner_id"),
            "deal_id": p.get("deal_id"),
            "tx_hash": p.get("tx_hash"),
            "memo": p.get("memo"),
            "metadata": p.get("metadata"),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
        }

    needs_review = [
        p for p in payments
        if p.get("status") in (STATUS_PENDING, STATUS_SUBMITTED)
    ]
    pending_review = [_review_row(p) for p in needs_review[:10]]

    return {
        **cfg.to_public_dict(),
        "accepted_networks": list_accepted_crypto_networks(),
        "payment_count": len(payments),
        "intent_count": len(intents),
        "by_status": by_status,
        "pending_review_count": len(needs_review),
        "pending_review": pending_review,
        "confirmed_total_usd": round(confirmed_usd, 2),
        "recent_payments": payments[:5],
        "recent_intents": intents[:5],
    }