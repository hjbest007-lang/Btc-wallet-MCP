"""Load only the adapters the operator enabled.

Factories are called for enabled coins and for no others. Each factory returns
a hot adapter, a cold watch-only adapter, and a broadcaster. The broadcaster
is kept by the payment gate; it is not stored on the adapter.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Callable

import bdkpython as bdk
from btc_wallet_mcp.keystore import read_seed_file
from btc_wallet_mcp.wallet import BtcWallet

from .adapters.base import AdapterError, ChainAdapter, UnconfiguredCold, UnconfiguredHot
from .adapters.bitcoin import (
    BitcoinAdapter,
    ReadOnlyClient,
    btc_config,
    esplora_url,
    open_watch_wallet,
)
from .adapters.evm import EvmAdapter, UrlEvmRead, account_from_mnemonic, broadcast_raw, checksum, default_rpc
from .adapters.litecoin import EsploraLtc, FileIndex, LitecoinAdapter, default_esplora
from .adapters.xrp import UrlXrpRead, XrpAdapter, default_rpc as xrp_rpc, submit_blob
from .config import AppConfig, ConfigError
from .gate import PaymentGate
from .policy import Policy
from .watch import load_watch_file


@dataclass
class Bundle:
    hot: ChainAdapter
    cold: ChainAdapter
    broadcaster: Callable


def _refuse_broadcast(_payload):
    raise AdapterError("broadcast blocked: this coin has no hot key")


def _guard_agent_has_no_cold_seed(cfg: AppConfig, coin_id: str) -> None:
    if cfg.mode != "agent":
        return
    root = cfg.coin_root(coin_id)
    if (root / "owner-cold" / "seed.enc").exists() or (cfg.cold_dir(coin_id) / "seed.enc").exists():
        raise ConfigError(
            f"cold {coin_id} seed is on the agent host; remove it and import watch-only data only"
        )


def _read_hot_secret(cfg: AppConfig, coin_id: str) -> str:
    path = cfg.hot_dir(coin_id) / "seed.enc"
    passphrase = cfg.env.get("WALLET_MCP_PASSPHRASE")
    if not passphrase:
        raise ConfigError("WALLET_MCP_PASSPHRASE not set")
    return read_seed_file(path, passphrase, missing_message=f"no hot {coin_id} wallet; run wallet-mcp init")


def build_bitcoin(cfg: AppConfig) -> Bundle:
    coin = cfg.coins["btc"]
    _guard_agent_has_no_cold_seed(cfg, "btc")
    hot_dir = cfg.hot_dir("btc")
    if not (hot_dir / "seed.enc").exists():
        hot = UnconfiguredHot("btc", coin.network, "sat", False)
        broadcaster = _refuse_broadcast
    else:
        if not cfg.env.get("WALLET_MCP_PASSPHRASE"):
            raise ConfigError("WALLET_MCP_PASSPHRASE not set")
        hot_dir.mkdir(parents=True, exist_ok=True)
        url = esplora_url(coin, cfg.env)
        inner = bdk.EsploraClient(url)
        wallet = BtcWallet(
            btc_config(coin, hot_dir, url),
            cfg.env["WALLET_MCP_PASSPHRASE"],
            client=ReadOnlyClient(inner),
        )
        hot = BitcoinAdapter(wallet, network=coin.network, role="hot")

        def broadcaster(tx, _inner=inner):
            _inner.broadcast(tx)

    cold = _bitcoin_cold(cfg, coin)
    return Bundle(hot, cold, broadcaster)


def _bitcoin_cold(cfg: AppConfig, coin) -> ChainAdapter:
    path = cfg.cold_dir("btc") / "watch.json"
    if not path.exists():
        return UnconfiguredCold("btc", coin.network, "sat", False)
    document = load_watch_file(path)
    if document.get("kind") != "bdk-descriptor":
        raise ConfigError("btc watch file must have kind bdk-descriptor")
    directory = cfg.cold_dir("btc")
    directory.mkdir(parents=True, exist_ok=True)
    # A read-only client is enough; cold never receives a broadcaster.
    url = esplora_url(coin, cfg.env)
    wallet = open_watch_wallet(
        coin, directory, document["external"], document["internal"],
        ReadOnlyClient(bdk.EsploraClient(url)), url,
    )
    return BitcoinAdapter(wallet, network=coin.network, role="cold")


def build_evm(coin_id: str):
    def build(cfg: AppConfig) -> Bundle:
        coin = cfg.coins[coin_id]
        _guard_agent_has_no_cold_seed(cfg, coin_id)
        chain_id = coin.chain_id
        if chain_id is None:
            raise ConfigError(f"{coin_id} has no chain id for {coin.network}")
        seed_path = cfg.hot_dir(coin_id) / "seed.enc"
        if not seed_path.exists():
            hot = UnconfiguredHot(coin_id, coin.network, "wei", False)
            broadcaster = _refuse_broadcast
        else:
            account = account_from_mnemonic(_read_hot_secret(cfg, coin_id))
            url = default_rpc(coin_id, coin.network, cfg.env, mainnet=coin.is_mainnet)
            hot = EvmAdapter(
                coin_id=coin_id, network=coin.network, chain_id=chain_id, role="hot",
                address=account.address, rpc=UrlEvmRead(url), max_fee_rate=coin.max_fee_rate,
                private_key=account.key,
            )

            def broadcaster(raw, _url=url):
                return broadcast_raw(_url, raw)

        cold = _evm_cold(cfg, coin)
        return Bundle(hot, cold, broadcaster)

    return build


def _evm_cold(cfg: AppConfig, coin) -> ChainAdapter:
    path = cfg.cold_dir(coin.coin_id) / "watch.json"
    if not path.exists():
        return UnconfiguredCold(coin.coin_id, coin.network, "wei", False)
    document = load_watch_file(path)
    if document.get("kind") != "address":
        raise ConfigError(f"{coin.coin_id} watch file must have kind address")
    url = default_rpc(coin.coin_id, coin.network, cfg.env, mainnet=coin.is_mainnet)
    return EvmAdapter(
        coin_id=coin.coin_id, network=coin.network, chain_id=coin.chain_id, role="cold",
        address=checksum(document["address"]), rpc=UrlEvmRead(url), max_fee_rate=coin.max_fee_rate,
        private_key=None,
    )


def build_xrp(cfg: AppConfig) -> Bundle:
    coin = cfg.coins["xrp"]
    _guard_agent_has_no_cold_seed(cfg, "xrp")
    seed_path = cfg.hot_dir("xrp") / "seed.enc"
    if not seed_path.exists():
        hot = UnconfiguredHot("xrp", coin.network, "drop", True)
        broadcaster = _refuse_broadcast
    else:
        from xrpl.wallet import Wallet

        seed = _read_hot_secret(cfg, "xrp")
        wallet = Wallet.from_seed(seed)
        url = xrp_rpc(coin.network, cfg.env, mainnet=coin.is_mainnet)
        hot = XrpAdapter(
            network=coin.network, role="hot", address=wallet.classic_address,
            rpc=UrlXrpRead(url), max_fee_rate=coin.max_fee_rate, seed=seed,
        )

        def broadcaster(blob, _url=url):
            submit_blob(_url, blob)
            return None

    cold = _xrp_cold(cfg, coin)
    return Bundle(hot, cold, broadcaster)


def _xrp_cold(cfg: AppConfig, coin) -> ChainAdapter:
    path = cfg.cold_dir("xrp") / "watch.json"
    if not path.exists():
        return UnconfiguredCold("xrp", coin.network, "drop", True)
    document = load_watch_file(path)
    if document.get("kind") != "address":
        raise ConfigError("xrp watch file must have kind address")
    url = xrp_rpc(coin.network, cfg.env, mainnet=coin.is_mainnet)
    return XrpAdapter(
        network=coin.network, role="cold", address=document["address"],
        rpc=UrlXrpRead(url), max_fee_rate=coin.max_fee_rate, seed=None,
    )


def build_ltc(cfg: AppConfig) -> Bundle:
    coin = cfg.coins["ltc"]
    _guard_agent_has_no_cold_seed(cfg, "ltc")
    seed_path = cfg.hot_dir("ltc") / "seed.enc"
    if not seed_path.exists():
        hot = UnconfiguredHot("ltc", coin.network, "litoshi", True)
        broadcaster = _refuse_broadcast
    else:
        mnemonic = _read_hot_secret(cfg, "ltc")
        url = default_esplora(coin.network, cfg.env, mainnet=coin.is_mainnet)
        hot_dir = cfg.hot_dir("ltc")
        hot_dir.mkdir(parents=True, exist_ok=True)
        hot = LitecoinAdapter(
            network=coin.network, role="hot", chain=EsploraLtc(url), max_fee_rate=coin.max_fee_rate,
            index_store=FileIndex(hot_dir / "index.json"), mnemonic=mnemonic,
        )

        def broadcaster(raw, _url=url):
            request = urllib.request.Request(
                _url + "/tx", data=raw.hex().encode(), headers={"Content-Type": "text/plain"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode().strip()

    cold = _ltc_cold(cfg, coin)
    return Bundle(hot, cold, broadcaster)


def _ltc_cold(cfg: AppConfig, coin) -> ChainAdapter:
    path = cfg.cold_dir("ltc") / "watch.json"
    if not path.exists():
        return UnconfiguredCold("ltc", coin.network, "litoshi", True)
    document = load_watch_file(path)
    if document.get("kind") != "bip84-xpub":
        raise ConfigError("ltc watch file must have kind bip84-xpub")
    url = default_esplora(coin.network, cfg.env, mainnet=coin.is_mainnet)
    directory = cfg.cold_dir("ltc")
    directory.mkdir(parents=True, exist_ok=True)
    return LitecoinAdapter(
        network=coin.network, role="cold", chain=EsploraLtc(url), max_fee_rate=coin.max_fee_rate,
        index_store=FileIndex(directory / "index.json"), xpub=document["xpub"],
    )


FACTORIES = {
    "btc": build_bitcoin,
    "eth": build_evm("eth"),
    "avax": build_evm("avax"),
    "xrp": build_xrp,
    "ltc": build_ltc,
}


class Runtime:
    def __init__(self, cfg: AppConfig, gate: PaymentGate, hot: dict, cold: dict):
        self.cfg = cfg
        self.gate = gate
        self.hot = hot
        self.cold = cold

    def enabled(self) -> list[str]:
        return [coin_id for coin_id in self.hot]

    def list_coins(self) -> dict:
        rows = []
        for coin_id, coin in self.cfg.coins.items():
            rows.append({
                "id": coin_id,
                "name": coin.spec.name,
                "symbol": coin.spec.symbol,
                "enabled": coin.enabled,
                "network": coin.network,
                "status": coin.spec.status,
                "experimental": coin.spec.experimental,
                "unit": coin.spec.unit,
                "loaded": coin_id in self.hot,
                "mainnet": coin.is_mainnet,
            })
        return {"coins": rows}

    def get_config(self) -> dict:
        secret = bool(self.cfg.env.get("WALLET_MCP_APPROVAL_SECRET"))
        coins = {}
        for coin_id, coin in self.cfg.coins.items():
            entry = {
                "enabled": coin.enabled,
                "network": coin.network,
                "status": coin.spec.status,
                "experimental": coin.spec.experimental,
                "unit": coin.spec.unit,
                "max_per_tx": coin.max_per_tx,
                "max_per_day": coin.max_per_day,
                "max_fee_rate": coin.max_fee_rate,
                "allowlist_enabled": bool(coin.allowlist),
                "mainnet": coin.is_mainnet,
            }
            if coin.enabled and coin_id in self.gate.policies:
                entry["spent_last_24h"] = self.gate.policies[coin_id].spent_last_24h()
                entry["sending_enabled"] = secret and self.hot[coin_id].can_sign
                entry["hot_initialized"] = self.hot[coin_id].can_sign
                entry["cold_watch_only"] = self.cold[coin_id].role == "cold" and not isinstance(
                    self.cold[coin_id], UnconfiguredCold
                )
            coins[coin_id] = entry
        return {"mode": self.cfg.mode, "data_dir": str(self.cfg.data_dir), "coins": coins}

    def _call(self, fn):
        try:
            return fn()
        except (AdapterError, ConfigError) as exc:
            return {"ok": False, "error": str(exc)}

    def hot_balance(self, coin: str) -> dict:
        return self._call(lambda: {"ok": True, "coin": coin, "role": "hot", "network": self.cfg.coins[coin].network,
                                   **self.hot[coin].balance()})

    def hot_address(self, coin: str) -> dict:
        return self._call(lambda: {
            "ok": True, "coin": coin, "role": "hot", "network": self.cfg.coins[coin].network,
            "address": self.hot[coin].receive_address(),
        })

    def cold_balance(self, coin: str) -> dict:
        return self._call(lambda: {"ok": True, "coin": coin, "role": "cold", "network": self.cfg.coins[coin].network,
                                   **self.cold[coin].balance()})

    def cold_address(self, coin: str) -> dict:
        return self._call(lambda: {
            "ok": True, "coin": coin, "role": "cold", "network": self.cfg.coins[coin].network,
            "address": self.cold[coin].receive_address(),
        })

    def transactions(self, coin: str, limit: int = 25) -> dict:
        adapter = self.hot[coin]
        if not adapter.supports_transactions:
            return {"ok": False, "error": f"{coin} transaction list is not available"}
        return self._call(lambda: {"ok": True, "transactions": adapter.list_transactions(limit)})


def _limit_provider(cfg: AppConfig, coin_id: str):
    def provider():
        # Re-read so a human-approved limit change applies without a restart,
        # and so a tampered raise is rejected on the next spend.
        from .config import load_config

        try:
            fresh = load_config(cfg.env)
        except ConfigError as exc:
            from .policy import PolicyError
            raise PolicyError(str(exc)) from None
        coin = fresh.coins[coin_id]
        return coin.max_per_tx, coin.max_per_day, coin.allowlist

    return provider


def build_runtime(cfg: AppConfig, factories: dict | None = None) -> Runtime:
    factories = factories or FACTORIES
    hot = {}
    cold = {}
    policies = {}
    broadcasters = {}
    ttl = {}
    for coin_id, coin in cfg.coins.items():
        if not coin.enabled:
            continue
        if coin_id not in factories:
            raise ConfigError(f"no adapter registered for {coin_id}")
        bundle = factories[coin_id](cfg)
        hot[coin_id] = bundle.hot
        cold[coin_id] = bundle.cold
        broadcasters[coin_id] = bundle.broadcaster
        policies[coin_id] = Policy(
            coin_id, coin.network, coin.spec.unit, cfg.policy_dir(coin_id),
            _limit_provider(cfg, coin_id), ttl=coin.spec.confirm_ttl_seconds,
        )
        ttl[coin_id] = coin.spec.confirm_ttl_seconds
    secret = lambda: cfg.env.get("WALLET_MCP_APPROVAL_SECRET")  # noqa: E731
    gate = PaymentGate(hot, policies, broadcasters, secret, ttl)
    return Runtime(cfg, gate, hot, cold)


def tool_names(runtime: Runtime) -> list[str]:
    names = ["list_coins", "get_config"]
    for coin_id in runtime.enabled():
        names.extend([
            f"{coin_id}_get_config",
            f"{coin_id}_hot_get_balance",
            f"{coin_id}_hot_get_address",
            f"{coin_id}_cold_get_balance",
            f"{coin_id}_cold_get_address",
            f"{coin_id}_prepare_payment",
            f"{coin_id}_send_payment",
        ])
        if runtime.hot[coin_id].supports_transactions:
            names.append(f"{coin_id}_list_transactions")
    return names
