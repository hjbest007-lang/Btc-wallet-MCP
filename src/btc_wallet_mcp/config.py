"""Configuration from environment variables. Defaults are safe (signet, small limits)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .guard import GuardError, validate_chain_url

MAINNET_OPT_IN = "I_UNDERSTAND_REAL_MONEY"

DEFAULT_ESPLORA = {
    "signet": "https://mempool.space/signet/api",
    "testnet": "https://blockstream.info/testnet/api",
    "testnet4": "https://mempool.space/testnet4/api",
    "regtest": "http://127.0.0.1:3002",
    "bitcoin": "https://mempool.space/api",
}


class ConfigError(Exception):
    pass


def _int(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer") from e
    if v < 0:
        raise ConfigError(f"{name} must be >= 0")
    return v


@dataclass(frozen=True)
class Config:
    network: str
    esplora_url: str
    data_dir: Path
    max_sats_per_tx: int
    max_sats_per_day: int
    max_fee_rate_sat_vb: int
    allowlist: frozenset[str] = field(default_factory=frozenset)
    confirm_ttl_seconds: int = 600
    max_failed_approvals: int = 5

    @property
    def is_mainnet(self) -> bool:
        return self.network == "bitcoin"


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    network = env.get("BTC_WALLET_NETWORK", "signet").strip().lower()
    if network == "mainnet":
        network = "bitcoin"
    if network not in DEFAULT_ESPLORA:
        raise ConfigError(f"unsupported network {network!r}")
    if network == "bitcoin" and env.get("BTC_WALLET_ALLOW_MAINNET") != MAINNET_OPT_IN:
        raise ConfigError(
            "mainnet requires BTC_WALLET_ALLOW_MAINNET=" + MAINNET_OPT_IN
        )
    url = env.get("BTC_WALLET_ESPLORA_URL") or DEFAULT_ESPLORA[network]
    try:
        url = validate_chain_url(url, mainnet=(network == "bitcoin"))
    except GuardError as exc:
        raise ConfigError(str(exc)) from exc
    base = Path(env.get("BTC_WALLET_DATA_DIR") or Path.home() / ".btc-wallet-mcp")
    allow = frozenset(
        a.strip() for a in env.get("BTC_WALLET_ALLOWLIST", "").split(",") if a.strip()
    )
    return Config(
        network=network,
        esplora_url=url.rstrip("/"),
        data_dir=base / network,
        max_sats_per_tx=_int(env, "BTC_WALLET_MAX_SATS_PER_TX", 50_000),
        max_sats_per_day=_int(env, "BTC_WALLET_MAX_SATS_PER_DAY", 100_000),
        max_fee_rate_sat_vb=max(1, _int(env, "BTC_WALLET_MAX_FEE_RATE", 50)),
        allowlist=allow,
        confirm_ttl_seconds=_int(env, "BTC_WALLET_CONFIRM_TTL", 600),
    )
