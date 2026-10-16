"""Create hot secrets and public watch-only exports. No network."""
from __future__ import annotations

import bdkpython as bdk

from btc_wallet_mcp.wallet import new_mnemonic

from .adapters.bitcoin import public_descriptors
from .adapters.evm import account_from_mnemonic
from .adapters.litecoin import export_xpub
from .config import ConfigError
from .watch import assert_watch_only


def new_secret(coin_id: str) -> str:
    if coin_id == "xrp":
        from xrpl.wallet import Wallet
        wallet = Wallet.create()
        if not wallet.seed:
            raise ConfigError("XRP key generation failed")
        return wallet.seed
    if coin_id not in {"btc", "eth", "avax", "ltc"}:
        raise ConfigError(f"unknown coin {coin_id}")
    return new_mnemonic()


def watch_document(coin_id: str, network: str, secret: str) -> dict:
    if coin_id == "btc":
        document = public_descriptors(secret, network)
    elif coin_id in {"eth", "avax"}:
        document = {"kind": "address", "address": account_from_mnemonic(secret).address}
    elif coin_id == "xrp":
        from xrpl.wallet import Wallet
        document = {"kind": "address", "address": Wallet.from_seed(secret).classic_address}
    elif coin_id == "ltc":
        document = {
            "kind": "bip84-xpub",
            "xpub": export_xpub(secret, network),
            "account_path": "m/84'/" + ("2'" if network == "mainnet" else "1'") + "/0'",
        }
    else:
        raise ConfigError(f"unknown coin {coin_id}")
    assert_watch_only(document)
    if coin_id == "btc":
        net = {"bitcoin": bdk.Network.BITCOIN, "testnet": bdk.Network.TESTNET,
               "testnet4": bdk.Network.TESTNET4, "signet": bdk.Network.SIGNET,
               "regtest": bdk.Network.REGTEST}[network]
        bdk.Descriptor(document["external"], net)
        bdk.Descriptor(document["internal"], net)
    return document
