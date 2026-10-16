"""Experimental Solana adapter (solders).

Hot keys use SLIP-0010 path ``m/44'/501'/0'/0'`` via solders. The adapter does
not broadcast during prepare. Cold wallets store a pubkey only. Status stays
experimental: the offline suite is mocked, and there is no public-cluster test.
"""
from __future__ import annotations

import json
import urllib.request

from embit import bip39
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.message import Message
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from .base import AdapterError, ChainAdapter, PreparedTransfer

DERIVATION = "m/44'/501'/0'/0'"
DEFAULT_RPC = {
    "devnet": "https://api.devnet.solana.com",
    "testnet": "https://api.testnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
}


def _require_https(url: str, *, mainnet: bool) -> str:
    if mainnet and not url.startswith("https://"):
        raise AdapterError("mainnet RPC URL must use https")
    if url.startswith("http://") and not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise AdapterError("RPC URL must use https")
    return url.rstrip("/")


def default_rpc(network: str, env, *, mainnet: bool) -> str:
    url = env.get("WALLET_MCP_SOL_RPC_URL") or DEFAULT_RPC[network]
    return _require_https(url, mainnet=mainnet)


def keypair_from_mnemonic(mnemonic: str) -> Keypair:
    seed = bip39.mnemonic_to_seed(mnemonic)
    return Keypair.from_seed_and_derivation_path(bytes(seed), DERIVATION)


def parse_pubkey(address: str) -> Pubkey:
    try:
        return Pubkey.from_string(address.strip())
    except Exception:
        raise AdapterError("invalid address for this network") from None


def rpc_call(url: str, method: str, params: list):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
    except Exception as exc:
        raise AdapterError(f"rpc failed: {type(exc).__name__}") from None
    if not isinstance(body, dict) or body.get("error"):
        raise AdapterError("rpc error")
    return body.get("result")


class UrlSolanaRead:
    def __init__(self, url: str):
        self.url = url

    def balance(self, address: str) -> int:
        result = rpc_call(self.url, "getBalance", [address])
        return int((result or {}).get("value") or 0)

    def blockhash(self) -> str:
        result = rpc_call(self.url, "getLatestBlockhash", [])
        value = (result or {}).get("value") or {}
        blockhash = value.get("blockhash")
        if not isinstance(blockhash, str) or not blockhash:
            raise AdapterError("rpc error")
        return blockhash

    def fee(self) -> int:
        return 5_000


def submit_transaction(url: str, raw: bytes) -> str:
    import base64
    encoded = base64.b64encode(raw).decode()
    result = rpc_call(url, "sendTransaction", [encoded, {"encoding": "base64"}])
    if not isinstance(result, str) or not result:
        raise AdapterError("broadcast rejected")
    return result


def _transfer_lamports(raw: bytes) -> tuple[str, int]:
    tx = Transaction.from_bytes(raw)
    instructions = tx.message.instructions
    if len(instructions) != 1:
        raise AdapterError("transaction does not match approved payment")
    ix = instructions[0]
    data = bytes(ix.data)
    if len(data) < 12 or int.from_bytes(data[:4], "little") != 2:
        raise AdapterError("transaction does not match approved payment")
    lamports = int.from_bytes(data[4:12], "little")
    account_index = ix.accounts[1]
    destination = str(tx.message.account_keys[account_index])
    return destination, lamports


class SolanaAdapter(ChainAdapter):
    coin_id = "sol"
    unit = "lamport"
    experimental = True
    supports_transactions = False

    def __init__(self, *, network: str, role: str, address: str, rpc, max_fee_rate: int,
                 mnemonic: str | None = None):
        self.network = network
        self.role = role
        self.address = str(parse_pubkey(address))
        self.rpc = rpc
        self.max_fee_rate = max_fee_rate
        self._keypair = None
        if role == "hot":
            if not mnemonic:
                raise AdapterError("hot Solana wallet has no seed")
            self._keypair = keypair_from_mnemonic(mnemonic)
            if str(self._keypair.pubkey()) != self.address:
                raise AdapterError("Solana seed does not match the hot address")
        self.can_sign = self._keypair is not None and role == "hot"

    def balance(self) -> dict:
        amount = int(self.rpc.balance(self.address))
        return {"confirmed": amount, "total": amount, "unit": "lamport"}

    def receive_address(self) -> str:
        return self.address

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        destination = str(parse_pubkey(address))
        if destination == self.address:
            raise AdapterError("refusing to pay to own address")
        blockhash = self.rpc.blockhash()
        Hash.from_string(blockhash)
        fee = int(self.rpc.fee())
        if fee < 1 or fee > self.max_fee_rate:
            raise AdapterError(f"network fee {fee} lamports exceeds max {self.max_fee_rate}")
        payload = {
            "from": self.address,
            "to": destination,
            "lamports": amount,
            "blockhash": blockhash,
            "fee": fee,
        }
        return PreparedTransfer(
            destination, amount, fee, json.dumps(payload, separators=(",", ":"), sort_keys=True), fee_rate=fee
        )

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self._keypair is None or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            payload = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        if set(payload) != {"blockhash", "fee", "from", "lamports", "to"}:
            raise AdapterError("transaction does not match approved payment")
        if (
            payload["from"] != self.address
            or payload["to"] != prepared.address
            or int(payload["lamports"]) != prepared.amount
            or int(payload["fee"]) != prepared.fee
        ):
            raise AdapterError("transaction does not match approved payment")
        blockhash = Hash.from_string(payload["blockhash"])
        instruction = transfer(TransferParams(
            from_pubkey=self._keypair.pubkey(),
            to_pubkey=parse_pubkey(prepared.address),
            lamports=prepared.amount,
        ))
        message = Message.new_with_blockhash([instruction], self._keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(message)
        tx.sign([self._keypair], blockhash)
        raw = bytes(tx)
        destination, lamports = _transfer_lamports(raw)
        if destination != prepared.address or lamports != prepared.amount:
            raise AdapterError("transaction does not match approved payment")
        broadcaster(raw)
        return str(tx.signatures[0])
