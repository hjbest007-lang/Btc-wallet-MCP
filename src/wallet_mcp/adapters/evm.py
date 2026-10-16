"""Ethereum and Avalanche C-chain. Same signer (eth-account), different chain id.

Avalanche C-chain addresses use the Ethereum BIP-44 path m/44'/60'/0'/0/0,
which is what MetaMask and most C-chain wallets derive. The adapter never
holds a broadcaster; ``sign`` only runs after the payment gate calls ``execute``.
"""
from __future__ import annotations

import json
import urllib.request

from eth_account import Account
from eth_utils import is_address, to_checksum_address

from .base import AdapterError, ChainAdapter, PreparedTransfer

Account.enable_unaudited_hdwallet_features()

TRANSFER_GAS = 21_000
DERIVATION = "m/44'/60'/0'/0/0"

DEFAULT_RPC = {
    ("eth", "sepolia"): "https://rpc.sepolia.org",
    ("eth", "mainnet"): "https://ethereum.publicnode.com",
    ("avax", "fuji"): "https://api.avax-test.network/ext/bc/C/rpc",
    ("avax", "mainnet"): "https://api.avax.network/ext/bc/C/rpc",
}


def account_from_mnemonic(mnemonic: str):
    return Account.from_mnemonic(mnemonic, account_path=DERIVATION)


def checksum(address: str) -> str:
    if not isinstance(address, str) or not is_address(address):
        raise AdapterError("invalid address for this network")
    return to_checksum_address(address)


def _require_https(url: str, *, mainnet: bool) -> str:
    if mainnet and not url.startswith("https://"):
        raise AdapterError("mainnet RPC URL must use https")
    if url.startswith("http://") and not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise AdapterError("RPC URL must use https")
    return url


def default_rpc(coin_id: str, network: str, env, *, mainnet: bool) -> str:
    var = f"WALLET_MCP_{coin_id.upper()}_RPC_URL"
    url = (env.get(var) or DEFAULT_RPC[(coin_id, network)]).rstrip("/")
    return _require_https(url, mainnet=mainnet)


def rpc_call(url: str, method: str, params: list):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(f"rpc failed: {type(exc).__name__}") from None
    if not isinstance(body, dict) or body.get("error"):
        raise AdapterError("rpc error")
    return body.get("result")


class UrlEvmRead:
    """Balance, nonce and fee quotes. No send method."""

    def __init__(self, url: str):
        self.url = url

    def get_balance(self, address: str) -> int:
        return int(rpc_call(self.url, "eth_getBalance", [address, "latest"]), 16)

    def get_transaction_count(self, address: str) -> int:
        return int(rpc_call(self.url, "eth_getTransactionCount", [address, "pending"]), 16)

    def suggest_fees(self) -> tuple[int, int]:
        gas_price = int(rpc_call(self.url, "eth_gasPrice", []), 16)
        try:
            priority = int(rpc_call(self.url, "eth_maxPriorityFeePerGas", []), 16)
        except AdapterError:
            priority = min(gas_price, 1_000_000_000)
        max_fee = max(gas_price, priority)
        return max_fee, min(priority, max_fee)


def broadcast_raw(url: str, raw: bytes) -> str:
    result = rpc_call(url, "eth_sendRawTransaction", ["0x" + raw.hex()])
    if not isinstance(result, str):
        raise AdapterError("broadcast rejected")
    return result[2:] if result.startswith("0x") else result


class EvmAdapter(ChainAdapter):
    unit = "wei"
    experimental = False
    supports_transactions = False

    def __init__(self, *, coin_id: str, network: str, chain_id: int, role: str, address: str,
                 rpc, max_fee_rate: int, private_key=None):
        self.coin_id = coin_id
        self.network = network
        self.chain_id = chain_id
        self.role = role
        self.address = checksum(address)
        self.rpc = rpc
        self.max_fee_rate = max_fee_rate
        self._key = private_key if role == "hot" else None
        self.can_sign = self._key is not None and role == "hot"

    def balance(self) -> dict:
        value = int(self.rpc.get_balance(self.address))
        return {"confirmed": value, "total": value, "unit": "wei"}

    def receive_address(self) -> str:
        return self.address

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        destination = checksum(address)
        if destination.lower() == self.address.lower():
            raise AdapterError("refusing to pay to own address")
        nonce = int(self.rpc.get_transaction_count(self.address))
        max_fee, priority = self.rpc.suggest_fees()
        max_fee, priority = int(max_fee), int(priority)
        if max_fee < 1 or priority < 0:
            raise AdapterError("invalid fee quote")
        if priority > max_fee:
            priority = max_fee
        fee = TRANSFER_GAS * max_fee
        if fee > self.max_fee_rate:
            raise AdapterError(f"network fee {fee} wei exceeds max {self.max_fee_rate}")
        payload = {
            "type": 2,
            "chainId": self.chain_id,
            "nonce": nonce,
            "gas": TRANSFER_GAS,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
            "to": destination,
            "value": amount,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return PreparedTransfer(destination, amount, fee, encoded, fee_rate=max_fee)

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self._key is None or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            tx = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        self._verify(tx, prepared)
        signed = Account.sign_transaction(tx, self._key)
        result = broadcaster(bytes(signed.raw_transaction))
        if isinstance(result, str) and result:
            return result[2:] if result.startswith("0x") else result
        digest = signed.hash.hex()
        return digest[2:] if digest.startswith("0x") else digest

    def _verify(self, tx: dict, prepared: PreparedTransfer) -> None:
        expected = json.loads(prepared.payload)
        if tx != expected:
            raise AdapterError("transaction does not match approved payment")
        if int(tx.get("chainId", -1)) != self.chain_id or int(tx.get("type", -1)) != 2:
            raise AdapterError("transaction does not match approved payment")
        if checksum(tx.get("to", "")) != prepared.address:
            raise AdapterError("transaction does not match approved payment")
        if int(tx.get("value", -1)) != prepared.amount or int(tx.get("gas", -1)) != TRANSFER_GAS:
            raise AdapterError("transaction does not match approved payment")
        fee = int(tx["gas"]) * int(tx["maxFeePerGas"])
        if fee != prepared.fee or int(tx["maxPriorityFeePerGas"]) > int(tx["maxFeePerGas"]):
            raise AdapterError("transaction does not match approved payment")
