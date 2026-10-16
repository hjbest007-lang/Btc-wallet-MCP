"""USDC and USDT as ERC-20 transfers on Ethereum and Avalanche C-chain.

The hot key is the parent chain's key (eth or avax). The token amount is what
the per-tx and daily limits count. Native gas is bound into the approval HMAC
as ``gas:<wei>`` and is also checked against the parent coin's limits by the
payment gate. ``execute`` is not overridden.
"""
from __future__ import annotations

import json

from eth_account import Account

from .base import AdapterError, ChainAdapter, PreparedTransfer
from .evm import checksum

TOKEN_GAS = 100_000
DECIMALS = 6

# Official Circle USDC and Tether USDT deployments.
# Sepolia USDT is the Aave v3 test token (not issued by Tether). There is no
# USDT deployment on Avalanche Fuji in this table; set WALLET_MCP_USDT_AVAX_TOKEN.
OFFICIAL_TOKENS = {
    ("usdc_eth", "sepolia"): "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    ("usdc_eth", "mainnet"): "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    ("usdt_eth", "sepolia"): "0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0",
    ("usdt_eth", "mainnet"): "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    ("usdc_avax", "fuji"): "0x5425890298aed601595a70AB815c96711a31Bc65",
    ("usdc_avax", "mainnet"): "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
    ("usdt_avax", "mainnet"): "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
}

PARENT = {
    "usdc_eth": "eth",
    "usdt_eth": "eth",
    "usdc_avax": "avax",
    "usdt_avax": "avax",
}


def token_contract(coin_id: str, network: str, env=None) -> str:
    override = "" if env is None else (env.get(f"WALLET_MCP_{coin_id.upper()}_TOKEN") or "")
    raw = override or OFFICIAL_TOKENS.get((coin_id, network))
    if not raw:
        raise AdapterError(
            f"no {coin_id} contract configured for {network}; "
            f"set WALLET_MCP_{coin_id.upper()}_TOKEN"
        )
    return checksum(raw)


def encode_transfer(address: str, amount: int) -> str:
    destination = checksum(address)
    padded = destination[2:].lower().zfill(64)
    value = hex(amount)[2:].zfill(64)
    return "0xa9059cbb" + padded + value


def _gas_wei(tx: dict) -> int:
    return int(tx["gas"]) * int(tx["maxFeePerGas"])


class Erc20Adapter(ChainAdapter):
    unit = "token"
    experimental = False
    supports_transactions = False

    def __init__(self, *, coin_id: str, network: str, chain_id: int, role: str, address: str,
                 token: str, rpc, max_fee_rate: int, private_key=None):
        if coin_id not in PARENT:
            raise AdapterError("unknown token coin")
        self.coin_id = coin_id
        self.parent_fee_coin = PARENT[coin_id]
        self.network = network
        self.chain_id = chain_id
        self.role = role
        self.address = checksum(address)
        self.token = checksum(token)
        self.rpc = rpc
        self.max_fee_rate = max_fee_rate
        self._key = private_key if role == "hot" else None
        self.can_sign = self._key is not None and role == "hot"

    def balance(self) -> dict:
        value = int(self.rpc.token_balance(self.token, self.address))
        return {"confirmed": value, "total": value, "unit": "token", "decimals": DECIMALS}

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
        fee = TOKEN_GAS * max_fee
        if fee > self.max_fee_rate:
            raise AdapterError(f"network fee {fee} wei exceeds max {self.max_fee_rate}")
        payload = {
            "type": 2,
            "chainId": self.chain_id,
            "nonce": nonce,
            "gas": TOKEN_GAS,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
            "to": self.token,
            "value": 0,
            "data": encode_transfer(destination, amount),
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        # Token limits see fee 0. Gas is bound here and re-checked by the gate
        # against the parent coin, and again inside sign.
        return PreparedTransfer(destination, amount, 0, encoded, fee_rate=max_fee, extra=f"gas:{fee}")

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self._key is None or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            tx = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        self._verify(tx, prepared)
        signing = dict(tx)
        signing["data"] = bytes.fromhex(signing["data"][2:])
        signed = Account.sign_transaction(signing, self._key)
        result = broadcaster(bytes(signed.raw_transaction))
        if isinstance(result, str) and result:
            return result[2:] if result.startswith("0x") else result
        digest = signed.hash.hex()
        return digest[2:] if digest.startswith("0x") else digest

    def _verify(self, tx: dict, prepared: PreparedTransfer) -> None:
        if tx != json.loads(prepared.payload):
            raise AdapterError("transaction does not match approved payment")
        if int(tx.get("chainId", -1)) != self.chain_id or checksum(tx.get("to", "")) != self.token:
            raise AdapterError("transaction does not match approved payment")
        if int(tx.get("value", -1)) != 0 or int(tx.get("gas", -1)) != TOKEN_GAS:
            raise AdapterError("transaction does not match approved payment")
        if tx.get("data") != encode_transfer(prepared.address, prepared.amount):
            raise AdapterError("transaction does not match approved payment")
        fee = _gas_wei(tx)
        if prepared.extra != f"gas:{fee}" or prepared.fee != 0:
            raise AdapterError("transaction does not match approved payment")
        if fee > self.max_fee_rate or int(tx["maxPriorityFeePerGas"]) > int(tx["maxFeePerGas"]):
            raise AdapterError("transaction does not match approved payment")
