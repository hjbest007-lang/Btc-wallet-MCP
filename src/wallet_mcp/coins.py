"""Coin catalog. Chain ids, default networks and default limits live in code.

A config file can select a known network and can only *lower* limits freely.
Raising a limit above the approved ceiling requires a human HMAC (see policy).
"""
from __future__ import annotations

from dataclasses import dataclass


MAINNET_OPT_IN = "I_UNDERSTAND_REAL_MONEY"


@dataclass(frozen=True)
class NetworkSpec:
    name: str
    mainnet: bool
    chain_id: int | None = None


@dataclass(frozen=True)
class CoinSpec:
    coin_id: str
    name: str
    symbol: str
    unit: str
    status: str  # "tested" or "experimental"
    default_network: str
    networks: dict[str, NetworkSpec]
    aliases: dict[str, str]
    default_max_per_tx: int
    default_max_per_day: int
    default_max_fee_rate: int
    confirm_ttl_seconds: int = 600

    @property
    def experimental(self) -> bool:
        return self.status != "tested"

    def canonical_network(self, raw: str) -> str:
        key = (raw or "").strip().lower()
        key = self.aliases.get(key, key)
        if key not in self.networks:
            known = ", ".join(sorted(self.networks))
            raise ValueError(f"unsupported {self.symbol} network {raw!r} (known: {known})")
        return key


def _nets(*items: NetworkSpec) -> dict[str, NetworkSpec]:
    return {item.name: item for item in items}


# Order is the checkbox order in `wallet-mcp configure`.
COIN_ORDER = ("btc", "eth", "avax", "xrp", "ltc")

COINS: dict[str, CoinSpec] = {
    "btc": CoinSpec(
        coin_id="btc",
        name="Bitcoin",
        symbol="BTC",
        unit="sat",
        status="tested",
        # Signet, not testnet3: same safe default as the Bitcoin-only server.
        default_network="signet",
        networks=_nets(
            NetworkSpec("signet", False),
            NetworkSpec("testnet", False),
            NetworkSpec("testnet4", False),
            NetworkSpec("regtest", False),
            NetworkSpec("bitcoin", True),
        ),
        aliases={"mainnet": "bitcoin"},
        default_max_per_tx=50_000,
        default_max_per_day=100_000,
        default_max_fee_rate=50,
    ),
    "eth": CoinSpec(
        coin_id="eth",
        name="Ethereum",
        symbol="ETH",
        unit="wei",
        status="tested",
        default_network="sepolia",
        networks=_nets(
            NetworkSpec("sepolia", False, chain_id=11155111),
            NetworkSpec("mainnet", True, chain_id=1),
        ),
        aliases={},
        default_max_per_tx=10**15,  # 0.001 ETH
        default_max_per_day=5 * 10**15,  # 0.005 ETH
        default_max_fee_rate=10**15,  # 0.001 ETH worst-case fee cap
    ),
    "avax": CoinSpec(
        coin_id="avax",
        name="Avalanche C-chain",
        symbol="AVAX",
        unit="wei",
        status="tested",
        default_network="fuji",
        networks=_nets(
            NetworkSpec("fuji", False, chain_id=43113),
            NetworkSpec("mainnet", True, chain_id=43114),
        ),
        aliases={},
        default_max_per_tx=10**16,  # 0.01 AVAX
        default_max_per_day=5 * 10**16,  # 0.05 AVAX
        default_max_fee_rate=10**15,  # 0.001 AVAX worst-case fee cap
    ),
    "xrp": CoinSpec(
        coin_id="xrp",
        name="XRP",
        symbol="XRP",
        unit="drop",
        status="tested",
        default_network="testnet",
        networks=_nets(
            NetworkSpec("testnet", False),
            NetworkSpec("devnet", False),
            NetworkSpec("mainnet", True),
        ),
        aliases={},
        default_max_per_tx=1_000_000,  # 1 XRP
        default_max_per_day=5_000_000,  # 5 XRP
        default_max_fee_rate=1_000,  # drops
    ),
    "ltc": CoinSpec(
        coin_id="ltc",
        name="Litecoin",
        symbol="LTC",
        unit="litoshi",
        status="tested",
        default_network="testnet",
        networks=_nets(
            NetworkSpec("testnet", False),
            NetworkSpec("mainnet", True),
        ),
        aliases={},
        default_max_per_tx=50_000,
        default_max_per_day=100_000,
        default_max_fee_rate=50,
    ),
}


def allow_mainnet_var(coin_id: str) -> str:
    return f"WALLET_MCP_{coin_id.upper()}_ALLOW_MAINNET"


def mainnet_opted_in(env, coin_id: str) -> bool:
    return env.get(allow_mainnet_var(coin_id)) == MAINNET_OPT_IN
