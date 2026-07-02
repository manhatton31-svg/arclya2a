"""Centralized configuration and secrets loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_CRYPTO_NETWORKS = frozenset({"base", "ethereum", "solana", "bnb"})
SUPPORTED_CRYPTO_TOKENS = frozenset({"USDC"})
_DEFAULT_CRYPTO_NETWORKS = ("base", "ethereum", "solana", "bnb")
_DEFAULT_CRYPTO_NETWORK = "base"
_DEFAULT_CRYPTO_TOKEN = "USDC"

# User-facing aliases normalized to canonical network ids.
_CRYPTO_NETWORK_ALIASES = {
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bnb",
    "binance": "bnb",
    "binance-smart-chain": "bnb",
}


def normalize_crypto_network(value: str) -> str | None:
    """Return canonical network id or None when unsupported."""
    key = value.strip().lower()
    if not key:
        return None
    key = _CRYPTO_NETWORK_ALIASES.get(key, key)
    if key in SUPPORTED_CRYPTO_NETWORKS:
        return key
    return None


def _wallet_env_key(network: str) -> str:
    return f"ARCLYA_CRYPTO_WALLET_{network.upper()}"

_dotenv_loaded = False

_ROOT_MARKERS = ("config/core.json", "agents/registry.json")


def resolve_project_root(*, start: Path | None = None) -> Path:
    """Find repository root when running from source or an installed package (e.g. Render)."""
    override = os.environ.get("ARCLYA_ROOT", "").strip()
    if override:
        return Path(override)

    candidates: list[Path] = []
    if start is not None:
        candidates.append(start)
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    candidates.append(Path.cwd())
    candidates.append(Path("/opt/render/project/src"))

    seen: set[Path] = set()
    for base in candidates:
        resolved = base.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if all((resolved / marker).is_file() for marker in _ROOT_MARKERS):
            return resolved

    return here.parents[2]


def project_root() -> Path:
    """Repository root (parent of src/)."""
    return resolve_project_root()


def _parse_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(raw: str | None, *, default: int, minimum: int = 0) -> int:
    if not raw:
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        return default


def _parse_float(raw: str | None, *, default: float, minimum: float = 0.0) -> float:
    if not raw:
        return default
    try:
        return max(minimum, float(raw.strip()))
    except ValueError:
        return default


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def load_dotenv(path: Path | None = None, *, override: bool = False) -> bool:
    """
    Load key=value pairs from a .env file into os.environ.

    By default only sets variables that are not already defined (local dev helper).
    Returns True when a file was read.
    """
    global _dotenv_loaded
    env_path = path or (project_root() / ".env")
    if not env_path.is_file():
        return False

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value

    _dotenv_loaded = True
    return True


def ensure_dotenv_loaded() -> None:
    """Load .env once when present (no-op in production when vars are injected)."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    # Avoid loading developer .env during pytest — tests control env via monkeypatch.
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("ARCLYA_SKIP_DOTENV"):
        _dotenv_loaded = True
        return
    load_dotenv()


def reset_dotenv_state() -> None:
    """Reset dotenv load flag (tests only)."""
    global _dotenv_loaded
    _dotenv_loaded = False


def _mask_wallet_address(address: str) -> str:
    if len(address) > 10:
        return f"{address[:6]}…{address[-4:]}"
    if address:
        return address[:4] + "…"
    return ""


@dataclass(frozen=True)
class CryptoPaymentSettings:
    enabled: bool
    wallets: dict[str, str]
    networks: tuple[str, ...]
    default_network: str
    token: str
    min_amount_usd: float
    intent_expiry_hours: int

    @property
    def wallet_address(self) -> str | None:
        """Default-network wallet (backward compatible)."""
        return self.wallets.get(self.default_network)

    @property
    def network(self) -> str:
        """Default network for intents when none is specified."""
        return self.default_network

    @property
    def configured(self) -> bool:
        return bool(self.wallets)

    def wallet_for(self, network: str) -> str | None:
        canonical = normalize_crypto_network(network)
        if not canonical:
            return None
        return self.wallets.get(canonical)

    def accepts_network(self, network: str) -> bool:
        return self.wallet_for(network) is not None

    def to_public_dict(self) -> dict[str, Any]:
        """Safe summary for dashboards (masked addresses only)."""
        wallets_masked = {
            net: _mask_wallet_address(addr)
            for net, addr in self.wallets.items()
            if addr
        }
        default_addr = self.wallet_address or ""
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "network": self.default_network,
            "networks": list(self.networks),
            "token": self.token,
            "wallet_address_masked": _mask_wallet_address(default_addr) or None,
            "wallets_masked": wallets_masked,
            "min_amount_usd": self.min_amount_usd,
            "intent_expiry_hours": self.intent_expiry_hours,
        }


@dataclass(frozen=True)
class ArclyaSettings:
    xai_api_key: str | None
    arclya_api_key: str | None
    arclya_operator_key: str | None
    public_url: str | None
    render_external_url: str | None
    port: int | None
    rate_limit_per_minute: int
    json_logs: bool
    tool_dry_run: bool
    tool_max_retries: int
    tool_retry_base_ms: int
    sandbox_max_keys_per_agent: int
    sandbox_max_register_per_ip_day: int
    sandbox_rate_limit_per_minute: int
    agent_register_rate_limit_per_minute: int
    agent_directory_rate_limit_per_minute: int
    agent_recommended_rate_limit_per_minute: int
    agent_rotate_key_rate_limit_per_minute: int
    agent_max_register_per_ip_per_day: int
    agent_require_email_verification_for_directory: bool
    agent_email_verification_token_hours: int
    agent_email_from: str | None
    agent_email_smtp_url: str | None
    agent_email_delivery: str
    sandbox_force_dry_run: bool
    sandbox_fast_chain: bool
    learning_scheduler_enabled: bool
    learning_interval_hours: int
    learning_min_deals: int
    learning_check_seconds: int
    auto_apply_low_risk: bool
    auto_apply_min_confidence: float
    isolation_min_actors: int
    graduation_webhook_url: str | None
    crypto: CryptoPaymentSettings

    def resolved_public_url(self, *, fallback: str | None = None) -> str | None:
        for value in (self.public_url, self.render_external_url):
            if value:
                return value.rstrip("/")
        return fallback.rstrip("/") if fallback else None


def _parse_crypto_network_list(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return _DEFAULT_CRYPTO_NETWORKS
    networks: list[str] = []
    for part in raw.split(","):
        canonical = normalize_crypto_network(part)
        if canonical and canonical not in networks:
            networks.append(canonical)
    return tuple(networks) if networks else _DEFAULT_CRYPTO_NETWORKS


def _build_crypto_settings() -> CryptoPaymentSettings:
    token = _env("ARCLYA_CRYPTO_TOKEN", _DEFAULT_CRYPTO_TOKEN).upper()
    if token not in SUPPORTED_CRYPTO_TOKENS:
        token = _DEFAULT_CRYPTO_TOKEN

    default_network = normalize_crypto_network(
        _env("ARCLYA_CRYPTO_NETWORK", _DEFAULT_CRYPTO_NETWORK),
    ) or _DEFAULT_CRYPTO_NETWORK
    legacy_wallet = _env("ARCLYA_CRYPTO_WALLET_ADDRESS") or None
    target_networks = _parse_crypto_network_list(_env("ARCLYA_CRYPTO_NETWORKS"))

    wallets: dict[str, str] = {}
    for network in target_networks:
        per_network = _env(_wallet_env_key(network)) or None
        if per_network:
            wallets[network] = per_network
        elif legacy_wallet and network == default_network:
            wallets[network] = legacy_wallet

    configured = bool(wallets)
    if default_network not in wallets and wallets:
        default_network = next(iter(wallets))

    enabled = _parse_bool(_env("ARCLYA_CRYPTO_ENABLED"), default=False) and configured
    return CryptoPaymentSettings(
        enabled=enabled,
        wallets=wallets,
        networks=tuple(wallets.keys()),
        default_network=default_network,
        token=token,
        min_amount_usd=_parse_float(_env("ARCLYA_CRYPTO_MIN_AMOUNT_USD"), default=10.0, minimum=1.0),
        intent_expiry_hours=_parse_int(
            _env("ARCLYA_CRYPTO_INTENT_EXPIRY_HOURS"), default=24, minimum=1,
        ),
    )


def _build_settings() -> ArclyaSettings:
    port_raw = _env("PORT")
    port = int(port_raw) if port_raw.isdigit() else None
    return ArclyaSettings(
        xai_api_key=_env("XAI_API_KEY") or None,
        arclya_api_key=_env("ARCLYA_API_KEY") or None,
        arclya_operator_key=_env("ARCLYA_OPERATOR_KEY") or None,
        public_url=_env("ARCLYA_PUBLIC_URL") or None,
        render_external_url=_env("RENDER_EXTERNAL_URL") or None,
        port=port,
        rate_limit_per_minute=_parse_int(_env("ARCLYA_RATE_LIMIT_PER_MINUTE"), default=60, minimum=1),
        json_logs=_parse_bool(_env("ARCLYA_JSON_LOGS"), default=False),
        tool_dry_run=_parse_bool(_env("ARCLYA_TOOL_DRY_RUN"), default=False),
        tool_max_retries=_parse_int(_env("ARCLYA_TOOL_MAX_RETRIES"), default=3, minimum=1),
        tool_retry_base_ms=_parse_int(_env("ARCLYA_TOOL_RETRY_BASE_MS"), default=500, minimum=50),
        sandbox_max_keys_per_agent=_parse_int(
            _env("ARCLYA_SANDBOX_MAX_KEYS_PER_AGENT"), default=2, minimum=1,
        ),
        sandbox_max_register_per_ip_day=_parse_int(
            _env("ARCLYA_SANDBOX_MAX_REGISTER_PER_IP_DAY"), default=5, minimum=1,
        ),
        sandbox_rate_limit_per_minute=_parse_int(
            _env("ARCLYA_SANDBOX_RATE_LIMIT_PER_MINUTE"), default=10, minimum=3,
        ),
        agent_register_rate_limit_per_minute=_parse_int(
            _env("ARCLYA_AGENT_REGISTER_RATE_LIMIT_PER_MINUTE"), default=5, minimum=1,
        ),
        agent_directory_rate_limit_per_minute=_parse_int(
            _env("ARCLYA_AGENT_DIRECTORY_RATE_LIMIT_PER_MINUTE"), default=30, minimum=1,
        ),
        agent_recommended_rate_limit_per_minute=_parse_int(
            _env("ARCLYA_AGENT_RECOMMENDED_RATE_LIMIT_PER_MINUTE"), default=20, minimum=1,
        ),
        agent_rotate_key_rate_limit_per_minute=_parse_int(
            _env("ARCLYA_AGENT_ROTATE_KEY_RATE_LIMIT_PER_MINUTE"), default=3, minimum=1,
        ),
        agent_max_register_per_ip_per_day=_parse_int(
            _env("ARCLYA_AGENT_MAX_REGISTER_PER_IP_DAY"), default=10, minimum=1,
        ),
        agent_require_email_verification_for_directory=_parse_bool(
            _env("ARCLYA_AGENT_REQUIRE_EMAIL_VERIFICATION"), default=True,
        ),
        agent_email_verification_token_hours=_parse_int(
            _env("ARCLYA_AGENT_EMAIL_VERIFICATION_HOURS"), default=24, minimum=1,
        ),
        agent_email_from=_env("ARCLYA_AGENT_EMAIL_FROM") or None,
        agent_email_smtp_url=_env("ARCLYA_AGENT_EMAIL_SMTP_URL") or None,
        agent_email_delivery=_env("ARCLYA_AGENT_EMAIL_DELIVERY") or "auto",
        sandbox_force_dry_run=_parse_bool(_env("ARCLYA_SANDBOX_FORCE_DRY_RUN"), default=True),
        sandbox_fast_chain=_parse_bool(_env("ARCLYA_REHEARSAL_MODE") or None, default=True),
        learning_scheduler_enabled=_parse_bool(_env("ARCLYA_LEARNING_SCHEDULER_ENABLED"), default=False),
        learning_interval_hours=_parse_int(_env("ARCLYA_LEARNING_INTERVAL_HOURS"), default=6, minimum=1),
        learning_min_deals=_parse_int(_env("ARCLYA_LEARNING_MIN_DEALS"), default=0, minimum=0),
        learning_check_seconds=_parse_int(_env("ARCLYA_LEARNING_CHECK_SECONDS"), default=300, minimum=30),
        auto_apply_low_risk=_parse_bool(_env("ARCLYA_AUTO_APPLY_LOW_RISK"), default=True),
        auto_apply_min_confidence=_parse_float(
            _env("ARCLYA_AUTO_APPLY_MIN_CONFIDENCE"), default=0.75, minimum=0.0,
        ),
        isolation_min_actors=_parse_int(_env("ARCLYA_ISOLATION_MIN_ACTORS"), default=2, minimum=1),
        graduation_webhook_url=_env("ARCLYA_GRADUATION_WEBHOOK_URL") or None,
        crypto=_build_crypto_settings(),
    )


def get_settings() -> ArclyaSettings:
    """Return current settings from environment (loads .env on first call)."""
    ensure_dotenv_loaded()
    return _build_settings()


def _core_config_base_url() -> str | None:
    core_path = project_root() / "config" / "core.json"
    if not core_path.is_file():
        return None
    try:
        import json

        core = json.loads(core_path.read_text(encoding="utf-8"))
        raw = (core.get("server") or {}).get("base_url")
        return str(raw).rstrip("/") if raw else None
    except (json.JSONDecodeError, OSError, TypeError, AttributeError):
        return None


def public_url_source() -> str:
    """Which configuration source supplies the public base URL."""
    settings = get_settings()
    if settings.public_url:
        return "ARCLYA_PUBLIC_URL"
    if settings.render_external_url:
        return "RENDER_EXTERNAL_URL"
    if _core_config_base_url():
        return "config/core.json"
    return "request_host"


def resolve_public_base_url(*, fallback: str | None = None) -> str:
    """
    Canonical public base URL for Agent Card, onboarding links, and verification emails.

    Priority: ARCLYA_PUBLIC_URL → RENDER_EXTERNAL_URL → fallback → config/core.json.
    """
    settings = get_settings()
    resolved = settings.resolved_public_url(fallback=fallback)
    if resolved:
        return resolved
    if fallback:
        return fallback.rstrip("/")
    return _core_config_base_url() or "http://127.0.0.1:8787"