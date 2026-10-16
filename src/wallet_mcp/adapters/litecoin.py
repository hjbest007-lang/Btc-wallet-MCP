"""Litecoin adapter (embit BIP84).

embit has no built-in Litecoin network, so address encoding uses the Litecoin
bech32 HRP (``tltc`` / ``ltc``) while BIP32 derivation uses embit's reviewed
secp256k1 path. Coin type is 2 on mainnet and 1 on testnet (SLIP-44).
Offline tests cover derivation and a mocked send. A public Esplora read is
opt-in and does not broadcast.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from embit import bip32, bip39, script
from embit.networks import NETWORKS
from embit.script import p2wpkh, witness_p2wpkh
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput

from .base import AdapterError, ChainAdapter, PreparedTransfer

DUST = 546
GAP = 20


@dataclass
class Utxo:
    txid: str
    vout: int
    value: int
    branch: int
    index: int


def hrp_for(network: str) -> str:
    return "ltc" if network == "mainnet" else "tltc"


def coin_type_for(network: str) -> str:
    return "2'" if network == "mainnet" else "1'"


def _version(network: str) -> tuple[bytes, bytes]:
    name = "main" if network == "mainnet" else "test"
    return NETWORKS[name]["xprv"], NETWORKS[name]["xpub"]


def _vbytes(inputs: int, outputs: int) -> int:
    return 11 + 68 * inputs + 31 * outputs


def select_coins(utxos: list[Utxo], amount: int, rate: int) -> tuple[list[Utxo], int, int]:
    if rate < 1:
        raise AdapterError("invalid fee rate")
    ordered = sorted(utxos, key=lambda item: item.value, reverse=True)
    chosen: list[Utxo] = []
    for utxo in ordered:
        chosen.append(utxo)
        total = sum(item.value for item in chosen)
        fee_with_change = rate * _vbytes(len(chosen), 2)
        change = total - amount - fee_with_change
        if change >= DUST:
            return chosen, fee_with_change, change
        fee_exact = rate * _vbytes(len(chosen), 1)
        remainder = total - amount - fee_exact
        if 0 <= remainder < DUST:
            return chosen, fee_exact + remainder, 0
    raise AdapterError("insufficient funds")


class EsploraLtc:
    """Read-only esplora view. Broadcast is intentionally not implemented here."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def _get(self, path: str):
        with urllib.request.urlopen(self.url + path, timeout=20) as response:
            return json.load(response)

    def utxos_for(self, address: str, branch: int, index: int) -> list[Utxo]:
        rows = self._get(f"/address/{address}/utxo")
        return [
            Utxo(row["txid"], int(row["vout"]), int(row["value"]), branch, index)
            for row in rows
        ]

    def fee_rate(self) -> int:
        try:
            data = self._get("/fee-estimates")
            quoted = data.get("6") or data.get("2") or 1
            return max(1, int(float(quoted) + 0.999))
        except Exception as exc:
            raise AdapterError(f"fee estimate failed: {type(exc).__name__}") from None


def export_xpub(mnemonic: str, network: str) -> str:
    """Account-level public key at m/84'/<coin type>/0'. No private key in the result."""
    private, public = _version(network)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic), version=private)
    account = root.derive(f"m/84'/{coin_type_for(network)}/0'")
    exported = account.to_public(version=public).to_base58()
    if "prv" in exported.lower():
        raise AdapterError("refusing to export a private key")
    return exported


def default_esplora(network: str, env, *, mainnet: bool) -> str:
    if network == "mainnet":
        url = env.get("WALLET_MCP_LTC_ESPLORA_URL") or "https://litecoinspace.org/api"
    else:
        url = env.get("WALLET_MCP_LTC_ESPLORA_URL") or "https://litecoinspace.org/testnet/api"
    if mainnet and not url.startswith("https://"):
        raise AdapterError("mainnet Esplora URL must use https")
    return url.rstrip("/")


class LitecoinAdapter(ChainAdapter):
    coin_id = "ltc"
    unit = "litoshi"
    experimental = False
    supports_transactions = False

    def __init__(self, *, network: str, role: str, chain, max_fee_rate: int, index_store,
                 mnemonic: str | None = None, xpub: str | None = None):
        self.network = network
        self.role = role
        self.chain = chain
        self.max_fee_rate = max_fee_rate
        self.index_store = index_store
        self._hrp = hrp_for(network)
        self._coin_type = coin_type_for(network)
        self._mnemonic = mnemonic if role == "hot" else None
        self._xpub = None
        if role == "cold":
            if not xpub or "prv" in xpub.lower():
                raise AdapterError("cold watch-only wallet cannot sign")
            key = bip32.HDKey.from_base58(xpub)
            if key.is_private:
                raise AdapterError("cold descriptor contains a private key")
            self._xpub = key
        self.can_sign = role == "hot" and bool(self._mnemonic)

    def _root(self):
        if not self._mnemonic:
            raise AdapterError("cold watch-only wallet cannot sign")
        seed = bip39.mnemonic_to_seed(self._mnemonic)
        private, _public = _version(self.network)
        return bip32.HDKey.from_seed(seed, version=private)

    def _hot_child(self, branch: int, index: int):
        return self._root().derive(f"m/84'/{self._coin_type}/0'/{branch}/{index}")

    def _address_at(self, branch: int, index: int) -> str:
        if self._xpub is not None:
            child = self._xpub.derive(f"m/{branch}/{index}")
            return p2wpkh(child).address({"bech32": self._hrp})
        return p2wpkh(self._hot_child(branch, index)).address({"bech32": self._hrp})

    def _indexes(self) -> dict:
        return self.index_store.load()

    def receive_address(self) -> str:
        indexes = self._indexes()
        index = int(indexes.get("receive", 0))
        address = self._address_at(0, index)
        indexes["receive"] = index + 1
        self.index_store.save(indexes)
        return address

    def _controlled(self) -> set[str]:
        indexes = self._indexes()
        receive = max(int(indexes.get("receive", 0)), 1)
        change = max(int(indexes.get("change", 0)), 1)
        found = set()
        for index in range(receive):
            found.add(self._address_at(0, index))
        for index in range(change):
            found.add(self._address_at(1, index))
        return found

    def balance(self) -> dict:
        total = 0
        for _branch, _index, address in self._window():
            total += sum(item.value for item in self.chain.utxos_for(address, _branch, _index))
        return {"confirmed": total, "total": total, "unit": "litoshi"}

    def _window(self):
        indexes = self._indexes()
        receive = int(indexes.get("receive", 0)) + GAP
        change = int(indexes.get("change", 0)) + 1
        for index in range(receive):
            yield 0, index, self._address_at(0, index)
        for index in range(change):
            yield 1, index, self._address_at(1, index)

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        destination = address.strip()
        if not destination.startswith(self._hrp + "1"):
            raise AdapterError("invalid address for this network")
        try:
            script.address_to_scriptpubkey(destination)
        except Exception:
            raise AdapterError("invalid address for this network") from None
        if destination in self._controlled():
            raise AdapterError("refusing to pay to own address")
        utxos: list[Utxo] = []
        for branch, index, addr in self._window():
            utxos.extend(self.chain.utxos_for(addr, branch, index))
        quoted = int(self.chain.fee_rate())
        if quoted > self.max_fee_rate:
            raise AdapterError(f"network fee {quoted} litoshi/vB exceeds max {self.max_fee_rate}")
        chosen, fee, change = select_coins(utxos, amount, quoted)
        outputs = [{"address": destination, "value": amount}]
        change_index = None
        if change:
            change_index = int(self._indexes().get("change", 0))
            outputs.append({"address": self._address_at(1, change_index), "value": change})
        payload = {
            "inputs": [
                {"txid": item.txid, "vout": item.vout, "value": item.value,
                 "branch": item.branch, "index": item.index}
                for item in chosen
            ],
            "outputs": outputs,
            "change_index": change_index,
            "fee_rate": quoted,
        }
        return PreparedTransfer(
            destination, amount, fee, json.dumps(payload, separators=(",", ":"), sort_keys=True), fee_rate=quoted
        )

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if not self.can_sign or self._mnemonic is None:
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            payload = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        outputs = payload.get("outputs") or []
        inputs = payload.get("inputs") or []
        foreign = [item for item in outputs if item["address"] != self._change_address(payload)]
        if len(foreign) != 1 or foreign[0]["address"] != prepared.address or int(foreign[0]["value"]) != prepared.amount:
            raise AdapterError("transaction does not match approved payment")
        change_value = 0
        if payload.get("change_index") is not None:
            change_addr = self._address_at(1, int(payload["change_index"]))
            change_outs = [item for item in outputs if item["address"] == change_addr]
            if len(change_outs) != 1:
                raise AdapterError("transaction does not match approved payment")
            change_value = int(change_outs[0]["value"])
        incoming = sum(int(item["value"]) for item in inputs)
        outgoing = sum(int(item["value"]) for item in outputs)
        if incoming - outgoing != prepared.fee or change_value != incoming - prepared.amount - prepared.fee:
            raise AdapterError("transaction does not match approved payment")
        tx = Transaction(version=2, vin=[], vout=[])
        keys = []
        for item in inputs:
            child = self._hot_child(int(item["branch"]), int(item["index"]))
            keys.append((child, int(item["value"]), p2wpkh(child)))
            tx.vin.append(TransactionInput(bytes.fromhex(item["txid"]), int(item["vout"])))
        for item in outputs:
            tx.vout.append(TransactionOutput(int(item["value"]), script.address_to_scriptpubkey(item["address"])))
        for index, (child, value, script_pub) in enumerate(keys):
            digest = tx.sighash_segwit(index, script_pub, value)
            signature = child.sign(digest)
            tx.vin[index].witness = witness_p2wpkh(signature, child, SIGHASH.ALL)
        raw = tx.serialize()
        broadcaster(raw)
        if payload.get("change_index") is not None:
            indexes = self._indexes()
            indexes["change"] = int(payload["change_index"]) + 1
            self.index_store.save(indexes)
        return tx.txid().hex()

    def _change_address(self, payload: dict) -> str:
        if payload.get("change_index") is None:
            return ""
        return self._address_at(1, int(payload["change_index"]))


class MemoryIndex:
    """Test double. Production uses ``FileIndex``."""

    def __init__(self, values: dict | None = None):
        self.values = dict(values or {"receive": 0, "change": 0})

    def load(self) -> dict:
        return dict(self.values)

    def save(self, values: dict) -> None:
        self.values = dict(values)


class FileIndex:
    def __init__(self, path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"receive": 0, "change": 0}
        return json.loads(self.path.read_text())

    def save(self, values: dict) -> None:
        from ..util import atomic_json
        atomic_json(self.path, values)
