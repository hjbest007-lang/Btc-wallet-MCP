"""XRP Ledger adapter (xrpl-py). Experimental: mocked unit tests only.

Destination tags are not supported. Submit happens only through the broadcaster
the payment gate passes into ``execute``. Signing itself is local.
"""
from __future__ import annotations

import json
import urllib.request

from xrpl.core.addresscodec import is_valid_classic_address
from xrpl.models.transactions import Payment
from xrpl.transaction import sign
from xrpl.wallet import Wallet

from .base import AdapterError, ChainAdapter, PreparedTransfer

DEFAULT_RPC = {
    "testnet": "https://s.altnet.rippletest.net:51234",
    "devnet": "https://s.devnet.rippletest.net:51234",
    "mainnet": "https://xrplcluster.com",
}
MIN_FEE = 12


def _classic(address: str) -> str:
    if not isinstance(address, str) or not is_valid_classic_address(address.strip()):
        raise AdapterError("invalid address for this network")
    return address.strip()


def _require_https(url: str, *, mainnet: bool) -> str:
    if mainnet and not url.startswith("https://"):
        raise AdapterError("mainnet RPC URL must use https")
    if url.startswith("http://") and not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise AdapterError("RPC URL must use https")
    return url.rstrip("/")


def default_rpc(network: str, env, *, mainnet: bool) -> str:
    url = env.get("WALLET_MCP_XRP_RPC_URL") or DEFAULT_RPC[network]
    return _require_https(url, mainnet=mainnet)


def rpc_call(url: str, method: str, params: dict) -> dict:
    payload = json.dumps({"method": method, "params": [params]}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
    except Exception as exc:
        raise AdapterError(f"rpc failed: {type(exc).__name__}") from None
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        raise AdapterError("rpc error")
    return result


class UrlXrpRead:
    def __init__(self, url: str):
        self.url = url

    def account(self, address: str) -> tuple[int, int | None]:
        result = rpc_call(self.url, "account_info", {"account": address, "ledger_index": "validated"})
        if result.get("error") == "actNotFound":
            return 0, None
        if result.get("error"):
            raise AdapterError("rpc error")
        data = result.get("account_data") or {}
        return int(data["Balance"]), int(data["Sequence"])

    def fee(self) -> int:
        result = rpc_call(self.url, "fee", {})
        drops = result.get("drops") or {}
        open_fee = drops.get("open_ledger_fee") or drops.get("minimum_fee") or MIN_FEE
        return max(MIN_FEE, int(open_fee))


def submit_blob(url: str, blob: str) -> None:
    result = rpc_call(url, "submit", {"tx_blob": blob})
    status = result.get("engine_result") or ""
    if result.get("error") or (status and not status.startswith("tes")):
        raise AdapterError("broadcast rejected")


class XrpAdapter(ChainAdapter):
    coin_id = "xrp"
    unit = "drop"
    experimental = True
    supports_transactions = False

    def __init__(self, *, network: str, role: str, address: str, rpc, max_fee_rate: int, seed: str | None = None):
        self.network = network
        self.role = role
        self.address = _classic(address)
        self.rpc = rpc
        self.max_fee_rate = max_fee_rate
        self._wallet = None
        if role == "hot":
            if not seed:
                raise AdapterError("hot XRP wallet has no seed")
            self._wallet = Wallet.from_seed(seed)
            if self._wallet.classic_address != self.address:
                raise AdapterError("XRP seed does not match the hot address")
        self.can_sign = self._wallet is not None and role == "hot"

    def balance(self) -> dict:
        amount, _sequence = self.rpc.account(self.address)
        return {"confirmed": amount, "total": amount, "unit": "drop"}

    def receive_address(self) -> str:
        return self.address

    def prepare(self, address: str, amount: int, memo: str = "") -> PreparedTransfer:
        destination = _classic(address)
        if destination == self.address:
            raise AdapterError("refusing to pay to own address")
        _balance, sequence = self.rpc.account(self.address)
        if sequence is None:
            raise AdapterError("account is not funded")
        fee = int(self.rpc.fee())
        if fee < MIN_FEE:
            fee = MIN_FEE
        if fee > self.max_fee_rate:
            raise AdapterError(f"network fee {fee} drops exceeds max {self.max_fee_rate}")
        payload = {
            "account": self.address,
            "destination": destination,
            "amount": str(amount),
            "fee": str(fee),
            "sequence": sequence,
        }
        return PreparedTransfer(
            destination, amount, fee, json.dumps(payload, separators=(",", ":"), sort_keys=True), fee_rate=fee
        )

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self._wallet is None or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            tx = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        allowed = {"account", "amount", "destination", "fee", "sequence"}
        if set(tx) != allowed:
            raise AdapterError("transaction does not match approved payment")
        if (
            tx["account"] != self.address
            or tx["destination"] != prepared.address
            or int(tx["amount"]) != prepared.amount
            or int(tx["fee"]) != prepared.fee
        ):
            raise AdapterError("transaction does not match approved payment")
        payment = Payment(
            account=tx["account"],
            destination=tx["destination"],
            amount=str(int(tx["amount"])),
            fee=str(int(tx["fee"])),
            sequence=int(tx["sequence"]),
        )
        signed = sign(payment, self._wallet)
        if (
            signed.destination != prepared.address
            or int(signed.amount) != prepared.amount
            or int(signed.fee) != prepared.fee
        ):
            raise AdapterError("transaction does not match approved payment")
        broadcaster(signed.blob())
        return signed.get_hash()
