"""Bitcoin adapter. Keys, descriptors and signing stay inside BDK.

Behaviour matches the Bitcoin-only server: BIP84, signet by default, the same
fee cap, and a PSBT is re-checked against the approved address, amount and fee
before it is signed. The Esplora client handed to the wallet cannot broadcast;
the payment gate passes a one-shot broadcaster only after approval.
"""
from __future__ import annotations

import bdkpython as bdk

from btc_wallet_mcp.config import DEFAULT_ESPLORA, Config as BtcConfig
from btc_wallet_mcp.wallet import NETWORKS, BtcWallet, WalletError

from .base import AdapterError, ChainAdapter, PreparedTransfer


def _reject_private(descriptor: str) -> None:
    lowered = descriptor.lower()
    for marker in ("xprv", "tprv", "yprv", "zprv", "vprv"):
        if marker in lowered:
            raise AdapterError("cold descriptor contains a private key")


class ReadOnlyClient:
    """BDK chain client with broadcast removed."""

    def __init__(self, inner):
        self._inner = inner

    def full_scan(self, *args, **kwargs):
        return self._inner.full_scan(*args, **kwargs)

    def get_fee_estimates(self):
        return self._inner.get_fee_estimates()

    def broadcast(self, *_args, **_kwargs):
        raise AdapterError("broadcast blocked: the payment gate has not authorized this spend")


class _GateBroadcaster:
    def __init__(self, broadcaster):
        self._broadcaster = broadcaster

    def broadcast(self, tx):
        self._broadcaster(tx)


def public_descriptors(mnemonic: str, network: str) -> dict:
    """Watch-only BIP84 descriptors. The secret key does not leave this function."""
    net = NETWORKS[network]
    secret = bdk.DescriptorSecretKey(net, bdk.Mnemonic.from_string(mnemonic), None)
    external = str(bdk.Descriptor.new_bip84(secret, bdk.KeychainKind.EXTERNAL, net))
    internal = str(bdk.Descriptor.new_bip84(secret, bdk.KeychainKind.INTERNAL, net))
    del secret
    _reject_private(external)
    _reject_private(internal)
    return {"kind": "bdk-descriptor", "external": external, "internal": internal}


def btc_config(coin, directory, esplora_url: str) -> BtcConfig:
    return BtcConfig(
        network=coin.network,
        esplora_url=esplora_url,
        data_dir=directory,
        max_sats_per_tx=coin.max_per_tx,
        max_sats_per_day=coin.max_per_day,
        max_fee_rate_sat_vb=coin.max_fee_rate,
        allowlist=coin.allowlist,
        confirm_ttl_seconds=coin.spec.confirm_ttl_seconds,
    )


def esplora_url(coin, env) -> str:
    override = env.get("WALLET_MCP_BTC_ESPLORA_URL") or env.get("BTC_WALLET_ESPLORA_URL")
    url = (override or DEFAULT_ESPLORA[coin.network]).rstrip("/")
    if coin.is_mainnet and not url.startswith("https://"):
        raise AdapterError("mainnet Esplora URL must use https")
    return url


def open_watch_wallet(coin, directory, external: str, internal: str, client, esplora_url: str):
    """Public-descriptor wallet. ``sign_and_broadcast`` is replaced and cannot spend."""
    _reject_private(external)
    _reject_private(internal)
    cfg = btc_config(coin, directory, esplora_url)
    wallet = BtcWallet.__new__(BtcWallet)
    wallet.cfg = cfg
    wallet.net = NETWORKS[coin.network]
    wallet.client = client
    wallet.persister = bdk.Persister.new_sqlite(str(directory / "wallet.sqlite"))
    wallet.w = bdk.Wallet(
        bdk.Descriptor(external, wallet.net),
        bdk.Descriptor(internal, wallet.net),
        wallet.net,
        wallet.persister,
    )

    def refuse(*_args, **_kwargs):
        raise WalletError("cold watch-only wallet cannot sign")

    wallet.sign_and_broadcast = refuse
    return wallet


class BitcoinAdapter(ChainAdapter):
    coin_id = "btc"
    unit = "sat"
    experimental = False
    supports_transactions = True

    def __init__(self, wallet, *, network: str, role: str):
        self.wallet = wallet
        self.network = network
        self.role = role
        self.can_sign = role == "hot"

    def balance(self) -> dict:
        self.wallet.sync()
        body = dict(self.wallet.balance())
        body["unit"] = "sat"
        return body

    def receive_address(self) -> str:
        return self.wallet.new_address()

    def list_transactions(self, limit: int = 25) -> list[dict]:
        self.wallet.sync()
        return self.wallet.transactions(max(1, min(int(limit), 100)))

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        try:
            self.wallet.sync()
            rate = self.wallet.fee_rate()
            payload, fee = self.wallet.build(address, amount, rate)
        except WalletError as exc:
            raise AdapterError(str(exc)) from None
        return PreparedTransfer(address, amount, int(fee), payload, fee_rate=int(rate))

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self.role != "hot" or not self.can_sign:
            raise AdapterError("cold watch-only wallet cannot sign")
        previous = self.wallet.client
        self.wallet.client = _GateBroadcaster(broadcaster)
        try:
            return self.wallet.sign_and_broadcast(
                prepared.payload, prepared.address, prepared.amount, prepared.fee
            )
        except WalletError as exc:
            raise AdapterError(str(exc)) from None
        finally:
            self.wallet.client = previous
