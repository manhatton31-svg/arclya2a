"""Tests for centralized settings and .env loading."""

from __future__ import annotations

from arclya2a.settings import (
    get_settings,
    load_dotenv,
    normalize_crypto_network,
    project_root,
    reset_dotenv_state,
    resolve_project_root,
)


def test_get_settings_reads_env(monkeypatch):
    monkeypatch.setenv("ARCLYA_API_KEY", "test-api-key")
    monkeypatch.setenv("ARCLYA_OPERATOR_KEY", "test-operator")
    monkeypatch.setenv("ARCLYA_RATE_LIMIT_PER_MINUTE", "120")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_BASE", "0xabc123def4567890")
    monkeypatch.setenv("ARCLYA_CRYPTO_WALLET_ETHEREUM", "0xeth123")
    monkeypatch.setenv("ARCLYA_CRYPTO_NETWORK", "base")
    monkeypatch.setenv("ARCLYA_CRYPTO_ENABLED", "1")

    settings = get_settings()
    assert settings.arclya_api_key == "test-api-key"
    assert settings.arclya_operator_key == "test-operator"
    assert settings.rate_limit_per_minute == 120
    assert settings.crypto.wallet_address == "0xabc123def4567890"
    assert settings.crypto.network == "base"
    assert settings.crypto.wallets["ethereum"] == "0xeth123"
    assert settings.crypto.token == "USDC"
    assert settings.crypto.configured is True
    assert settings.crypto.enabled is True


def test_normalize_crypto_network_aliases():
    assert normalize_crypto_network("eth") == "ethereum"
    assert normalize_crypto_network("bsc") == "bnb"
    assert normalize_crypto_network("BASE") == "base"
    assert normalize_crypto_network("invalid") is None


def test_load_dotenv_does_not_override_existing(monkeypatch, tmp_path):
    reset_dotenv_state()
    env_file = tmp_path / ".env"
    env_file.write_text("ARCLYA_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ARCLYA_API_KEY", "from-env")

    load_dotenv(env_file)
    assert get_settings().arclya_api_key == "from-env"


def test_load_dotenv_sets_missing_vars(monkeypatch, tmp_path):
    reset_dotenv_state()
    monkeypatch.delenv("ARCLYA_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ARCLYA_API_KEY=from-dotenv\n# comment\nARCLYA_CRYPTO_NETWORK=ethereum\n",
        encoding="utf-8",
    )

    load_dotenv(env_file)
    settings = get_settings()
    assert settings.arclya_api_key == "from-dotenv"
    assert settings.crypto.network == "ethereum"


def test_resolved_public_url_prefers_arclya_public(monkeypatch):
    monkeypatch.setenv("ARCLYA_PUBLIC_URL", "https://arclya.example")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://render.example")
    settings = get_settings()
    assert settings.resolved_public_url() == "https://arclya.example"


def test_project_root_finds_repo_markers():
    root = project_root()
    assert (root / "config" / "core.json").is_file()
    assert (root / "agents" / "registry.json").is_file()


def test_resolve_project_root_honors_arclya_root(monkeypatch, tmp_path):
    fake = tmp_path / "repo"
    (fake / "config").mkdir(parents=True)
    (fake / "agents").mkdir(parents=True)
    (fake / "config" / "core.json").write_text("{}", encoding="utf-8")
    (fake / "agents" / "registry.json").write_text('{"agents":[]}', encoding="utf-8")
    monkeypatch.setenv("ARCLYA_ROOT", str(fake))
    assert resolve_project_root() == fake