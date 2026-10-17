"""XRP Ledger adapter (xrpl-py).

Submit happens only through the broadcaster the payment gate passes into
``execute``. Signing itself is local. A destination tag, when present, is part
of the unsigned payload and of the approval HMAC. Known exchange deposit
addresses require one. Nothing in the unit tests submits a transaction.
"""
from __future__ import annotations

import json

from xrpl.core.addresscodec import is_valid_classic_address
from xrpl.models.transactions import Payment
from xrpl.transaction import sign
from xrpl.wallet import Wallet

from btc_wallet_mcp.guard import GuardError, open_chain, validate_chain_url

from .base import AdapterError, ChainAdapter, PreparedTransfer

# lsfRequireDestTag. Accounts with this flag only accept payments that carry a tag.
REQUIRE_DEST_TAG = 0x00020000
TAG_MAX = 2**32 - 1

# Published shared deposit addresses. A missing tag drops the payment into an
# uncredited exchange account. Sources: Kraken support article 360000184443,
# Bitstamp ripple.txt (hotwallets, "XRP deposits only").
KNOWN_EXCHANGE_ADDRESSES = {
    "rLHzPsX6oXkzU2qL12kHCH8G8cnZv1rBJh": "Kraken",
    "rDsbeomae4FXwgQTJp9Rs64Qg9vDiTCdBv": "Bitstamp",
}

DEFAULT_RPC = {
    "testnet": "https://s.altnet.rippletest.net:51234",
    "devnet": "https://s.devnet.rippletest.net:51234",
    "mainnet": "https://xrplcluster.com",
}
MIN_FEE = 12
# secp256k1 BIP44, the path Ledger, Xumm, and xrpl.js fromMnemonic use.
# The key has no XRP family seed; the BIP39 mnemonic is the backup.
BIP44_PATH = "m/44'/144'/0'/0/0"


def _classic(address: str) -> str:
    if not isinstance(address, str) or not is_valid_classic_address(address.strip()):
        raise AdapterError("invalid address for this network")
    return address.strip()


def _require_https(url: str, *, mainnet: bool) -> str:
    try:
        return validate_chain_url(url, mainnet=mainnet)
    except GuardError as exc:
        raise AdapterError(str(exc)) from None


def exchange_addresses(env=None) -> dict[str, str]:
    """Built-in exchange addresses plus any the operator appended.

    ``WALLET_MCP_XRP_TAG_REQUIRED`` is a comma-separated list of classic
    addresses. It can only add addresses that require a tag.
    """
    found = dict(KNOWN_EXCHANGE_ADDRESSES)
    extra = "" if env is None else (env.get("WALLET_MCP_XRP_TAG_REQUIRED") or "")
    for part in extra.split(","):
        address = part.strip()
        if not address:
            continue
        if not is_valid_classic_address(address):
            raise AdapterError("invalid address in WALLET_MCP_XRP_TAG_REQUIRED")
        found.setdefault(address, "configured exchange")
    return found


def parse_destination_tag(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError("destination tag must be an integer from 0 to 4294967295")
    if value < 0 or value > TAG_MAX:
        raise AdapterError("destination tag must be an integer from 0 to 4294967295")
    return value


def tag_extra(tag: int | None) -> str:
    return "" if tag is None else f"tag:{tag}"


def _expected_tag(extra: str) -> int | None:
    if not extra:
        return None
    if not extra.startswith("tag:"):
        raise AdapterError("transaction does not match approved payment")
    try:
        tag = int(extra.split(":", 1)[1])
    except ValueError:
        raise AdapterError("transaction does not match approved payment") from None
    if isinstance(tag, bool) or tag < 0 or tag > TAG_MAX:
        raise AdapterError("transaction does not match approved payment")
    return tag


def default_rpc(network: str, env, *, mainnet: bool) -> str:
    url = env.get("WALLET_MCP_XRP_RPC_URL") or DEFAULT_RPC[network]
    return _require_https(url, mainnet=mainnet)


def rpc_call(url: str, method: str, params: dict) -> dict:
    payload = json.dumps({"method": method, "params": [params]}).encode()
    try:
        with open_chain(url, data=payload, headers={"Content-Type": "application/json"}, timeout=20) as response:
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

    def requires_destination_tag(self, address: str) -> bool:
        result = rpc_call(self.url, "account_info", {"account": address, "ledger_index": "validated"})
        if result.get("error"):
            return False
        flags = int((result.get("account_data") or {}).get("Flags") or 0)
        return bool(flags & REQUIRE_DEST_TAG)


def wallet_from_mnemonic(mnemonic: str):
    """First XRP account at ``m/44'/144'/0'/0/0``. Empty BIP39 passphrase.

    The returned wallet has no family seed. Signing uses the secp256k1 key,
    which XRPL encodes with a ``00`` prefix.
    """
    words = (mnemonic or "").split()
    if len(words) not in (12, 15, 18, 21, 24):
        raise AdapterError("XRP master key must be a BIP39 mnemonic")
    from embit import bip32, bip39
    from embit.networks import NETWORKS

    seed = bip39.mnemonic_to_seed(mnemonic)
    root = bip32.HDKey.from_seed(seed, version=NETWORKS["main"]["xprv"])
    child = root.derive(BIP44_PATH)
    secret = child.key.secret
    public = child.key.sec()
    wallet = Wallet(public.hex().upper(), "00" + secret.hex().upper())
    del secret, public
    return wallet


def submit_blob(url: str, blob: str) -> None:
    result = rpc_call(url, "submit", {"tx_blob": blob})
    status = result.get("engine_result") or ""
    if result.get("error") or (status and not status.startswith("tes")):
        raise AdapterError("broadcast rejected")


class XrpAdapter(ChainAdapter):
    coin_id = "xrp"
    unit = "drop"
    experimental = False
    supports_transactions = False

    def __init__(self, *, network: str, role: str, address: str, rpc, max_fee_rate: int,
                 seed: str | None = None, wallet=None, tag_required: dict | None = None):
        self.network = network
        self.role = role
        self.address = _classic(address)
        self.rpc = rpc
        self.max_fee_rate = max_fee_rate
        self.tag_required = dict(KNOWN_EXCHANGE_ADDRESSES if tag_required is None else tag_required)
        self._wallet = None
        if role == "hot":
            if wallet is None:
                if not seed:
                    raise AdapterError("hot XRP wallet has no seed")
                wallet = Wallet.from_seed(seed)
            self._wallet = wallet
            if self._wallet.classic_address != self.address:
                raise AdapterError("XRP seed does not match the hot address")
        self.can_sign = self._wallet is not None and role == "hot"

    def balance(self) -> dict:
        amount, _sequence = self.rpc.account(self.address)
        return {"confirmed": amount, "total": amount, "unit": "drop"}

    def receive_address(self) -> str:
        return self.address

    def _tag_required_reason(self, destination: str) -> str | None:
        label = self.tag_required.get(destination)
        flagged = False
        checker = getattr(self.rpc, "requires_destination_tag", None)
        if checker is not None and checker(destination):
            flagged = True
        if label is None and not flagged:
            return None
        return label or "an account with RequireDestTag set"

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        destination = _classic(address)
        if destination == self.address:
            raise AdapterError("refusing to pay to own address")
        tag = parse_destination_tag(destination_tag)
        reason = self._tag_required_reason(destination)
        if reason is not None and tag is None:
            raise AdapterError(f"destination tag is required for {reason}")
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
        if tag is not None:
            payload["destination_tag"] = tag
        return PreparedTransfer(
            destination, amount, fee, json.dumps(payload, separators=(",", ":"), sort_keys=True),
            fee_rate=fee, extra=tag_extra(tag),
        )

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        if self._wallet is None or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        try:
            tx = json.loads(prepared.payload)
        except json.JSONDecodeError:
            raise AdapterError("transaction does not match approved payment") from None
        expected_tag = _expected_tag(prepared.extra)
        allowed = {"account", "amount", "destination", "fee", "sequence"}
        if expected_tag is not None:
            allowed.add("destination_tag")
        if set(tx) != allowed:
            raise AdapterError("transaction does not match approved payment")
        payload_tag = tx.get("destination_tag")
        if expected_tag is None and payload_tag is not None:
            raise AdapterError("transaction does not match approved payment")
        if expected_tag is not None and int(payload_tag) != expected_tag:
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
            destination_tag=expected_tag,
        )
        signed = sign(payment, self._wallet)
        signed_tag = signed.destination_tag
        if expected_tag is None:
            signed_tag = None if signed_tag in (None, 0) else signed_tag
        if (
            signed.destination != prepared.address
            or int(signed.amount) != prepared.amount
            or int(signed.fee) != prepared.fee
            or signed_tag != expected_tag
        ):
            raise AdapterError("transaction does not match approved payment")
        broadcaster(signed.blob())
        return signed.get_hash()
