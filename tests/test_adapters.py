"""Adapter unit tests. Send paths use mocks; nothing here touches a network."""
import json
import urllib.request

import pytest
from eth_account import Account

from btc_wallet_mcp.config import load_config as load_btc
from btc_wallet_mcp.keystore import write_seed_file
from btc_wallet_mcp.wallet import BtcWallet, WalletError
from wallet_mcp.adapters.base import AdapterError, PreparedTransfer
from wallet_mcp.adapters.bitcoin import BitcoinAdapter, ReadOnlyClient, open_watch_wallet, public_descriptors
from wallet_mcp.adapters.evm import EvmAdapter
from wallet_mcp.adapters.litecoin import LitecoinAdapter, MemoryIndex, Utxo, export_xpub
from wallet_mcp.adapters.xrp import XrpAdapter
from wallet_mcp.config import load_config
from wallet_mcp.gate import PaymentGate, _issue
from wallet_mcp.policy import Policy, approval_code
from xrpl.wallet import Wallet

MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
OTHER = "legal winner thank year wave sausage worth useful legal winner thank yellow"
PW = "correct horse battery"
SECRET = "human-only-secret-123"
ETH_MNEMONIC = "test test test test test test test test test test test junk"
ETH_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
XRP_SEED = "sEdTM1uX8pu2do5XvTnutH6HsouMaM2"
XRP_ADDRESS = "rG31cLyErnqeVj2eomEjBZtq7PYaupGYzL"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


class NoNet:
    def get_fee_estimates(self):
        return {6: 1.0}

    def full_scan(self, *_args, **_kwargs):
        raise AssertionError("network")

    def broadcast(self, _tx):
        raise AssertionError("broadcast")


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "txid-from-gate"


def test_bitcoin_address_matches_bip84_and_cold_cannot_sign(tmp_path):
    btc_cfg = load_btc({"BTC_WALLET_DATA_DIR": str(tmp_path / "hot"), "BTC_WALLET_NETWORK": "signet"})
    write_seed_file(btc_cfg.data_dir / "seed.enc", MNEMONIC, PW, n=2**10)
    wallet = BtcWallet(btc_cfg, PW, client=ReadOnlyClient(NoNet()))
    hot = BitcoinAdapter(wallet, network="signet", role="hot")
    assert hot.receive_address() == "tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl"
    with pytest.raises(AdapterError):
        ReadOnlyClient(NoNet()).broadcast("nope")

    app = load_config({"WALLET_MCP_DATA_DIR": str(tmp_path), "WALLET_MCP_CONFIG": str(tmp_path / "c.json")})
    descriptors = public_descriptors(MNEMONIC, "signet")
    assert "prv" not in json.dumps(descriptors)
    cold_dir = tmp_path / "cold"
    cold_dir.mkdir()
    watched = open_watch_wallet(
        app.coins["btc"], cold_dir, descriptors["external"], descriptors["internal"],
        ReadOnlyClient(NoNet()), "https://mempool.space/signet/api",
    )
    cold = BitcoinAdapter(watched, network="signet", role="cold")
    assert cold.receive_address() == "tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl"
    assert cold.can_sign is False
    spy = Spy()
    with pytest.raises(AdapterError, match="cannot sign"):
        cold.sign(PreparedTransfer("tb1q", 1, 1, "psbt"), spy)
    with pytest.raises(WalletError, match="cannot sign"):
        watched.sign_and_broadcast("psbt", "tb1q", 1, 1)
    assert spy.calls == []


class EvmRpc:
    def __init__(self, max_fee=1_000_000_000):
        self.max_fee = max_fee

    def get_balance(self, _address):
        return 10**18

    def get_transaction_count(self, _address):
        return 1

    def suggest_fees(self):
        return self.max_fee, 1


def _account():
    Account.enable_unaudited_hdwallet_features()
    return Account.from_mnemonic(ETH_MNEMONIC)


def _evm(coin, network, chain_id, rpc=None):
    account = _account()
    return EvmAdapter(
        coin_id=coin, network=network, chain_id=chain_id, role="hot", address=account.address,
        rpc=rpc or EvmRpc(), max_fee_rate=10**16, private_key=account.key,
    )


def test_evm_vector_and_mocked_send_through_gate(tmp_path):
    eth = _evm("eth", "sepolia", 11155111)
    assert eth.receive_address() == ETH_ADDRESS
    avax = _evm("avax", "fuji", 43113)
    assert avax.receive_address() == ETH_ADDRESS
    assert json.loads(avax.prepare("0x0000000000000000000000000000000000000001", 5).payload)["chainId"] == 43113

    spy = Spy()
    policy = Policy("eth", "sepolia", "wei", tmp_path, lambda: (10**18, 10**18, frozenset()))
    gate = PaymentGate({"eth": eth}, {"eth": policy}, {"eth": spy}, lambda: SECRET)
    prepared = gate.prepare_payment("eth", "0x0000000000000000000000000000000000000001", 1000)
    assert prepared["ok"] and spy.calls == []
    refused = gate.send_payment("eth", prepared["confirm_id"], "")
    assert "invalid" in refused["error"] and spy.calls == []
    code = approval_code(SECRET, "eth", "sepolia", "hot", prepared["confirm_id"], prepared["address"], prepared["amount"], prepared["fee"])
    sent = gate.send_payment("eth", prepared["confirm_id"], code)
    assert sent["ok"] and sent["txid"] == "txid-from-gate"
    assert len(spy.calls) == 1 and spy.calls[0][0] == 2  # EIP-1559 type byte

    over = gate.prepare_payment("eth", ETH_ADDRESS, 1)
    assert "own address" in over["error"]
    huge = EvmAdapter(
        coin_id="eth", network="sepolia", chain_id=11155111, role="hot", address=eth.address,
        rpc=EvmRpc(max_fee=10**20), max_fee_rate=10**15, private_key=_account().key,
    )
    with pytest.raises(AdapterError, match="fee"):
        huge.prepare("0x0000000000000000000000000000000000000001", 1)


def test_evm_cold_has_no_key(tmp_path):
    cold = EvmAdapter(
        coin_id="eth", network="sepolia", chain_id=11155111, role="cold", address=ETH_ADDRESS,
        rpc=EvmRpc(), max_fee_rate=10**16, private_key=None,
    )
    assert cold.can_sign is False and cold._key is None
    prepared = cold.prepare("0x0000000000000000000000000000000000000001", 1)
    spy = Spy()
    with pytest.raises(AdapterError, match="cannot sign"):
        cold.sign(prepared, spy)
    assert spy.calls == []


class XrpRpc:
    def account(self, _address):
        return 5_000_000, 4

    def fee(self):
        return 12


def test_xrp_mocked_send_and_limit(tmp_path):
    wallet = Wallet.from_seed(XRP_SEED)
    assert wallet.classic_address == XRP_ADDRESS
    adapter = XrpAdapter(
        network="testnet", role="hot", address=wallet.classic_address, rpc=XrpRpc(),
        max_fee_rate=100, seed=wallet.seed,
    )
    spy = Spy()
    policy = Policy("xrp", "testnet", "drop", tmp_path, lambda: (10_000, 10_000, frozenset()))
    gate = PaymentGate({"xrp": adapter}, {"xrp": policy}, {"xrp": spy}, lambda: SECRET)
    over = gate.prepare_payment("xrp", "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", 50_000)
    assert "per-tx" in over["error"] and spy.calls == []
    prepared = gate.prepare_payment("xrp", "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", 1000)
    assert prepared["ok"] and prepared["fee"] == 12 and spy.calls == []
    assert not gate.send_payment("xrp", prepared["confirm_id"], "nope")["ok"]
    assert spy.calls == []
    code = approval_code(
        SECRET, "xrp", "testnet", "hot", prepared["confirm_id"], prepared["address"], prepared["amount"], prepared["fee"]
    )
    sent = gate.send_payment("xrp", prepared["confirm_id"], code)
    assert sent["ok"] and len(spy.calls) == 1 and isinstance(spy.calls[0], str)

    cold = XrpAdapter(
        network="testnet", role="cold", address=XRP_ADDRESS, rpc=XrpRpc(), max_fee_rate=100, seed=None,
    )
    with pytest.raises(AdapterError, match="cannot sign"):
        cold.sign(prepared_from(prepared), spy)
    assert len(spy.calls) == 1


KRAKEN = "rLHzPsX6oXkzU2qL12kHCH8G8cnZv1rBJh"


def test_xrp_exchange_tag_is_required_and_bound_into_the_code(tmp_path):
    wallet = Wallet.from_seed(XRP_SEED)
    adapter = XrpAdapter(
        network="testnet", role="hot", address=wallet.classic_address, rpc=XrpRpc(),
        max_fee_rate=100, seed=wallet.seed,
    )
    spy = Spy()
    policy = Policy("xrp", "testnet", "drop", tmp_path, lambda: (2_000_000, 2_000_000, frozenset()))
    gate = PaymentGate({"xrp": adapter}, {"xrp": policy}, {"xrp": spy}, lambda: SECRET)
    missing = gate.prepare_payment("xrp", KRAKEN, 1000)
    assert not missing["ok"] and "destination tag is required" in missing["error"] and "Kraken" in missing["error"]
    assert spy.calls == []
    rejected = gate.prepare_payment("xrp", KRAKEN, 1000, destination_tag=-1)
    assert not rejected["ok"] and spy.calls == []
    prepared = gate.prepare_payment("xrp", KRAKEN, 1000, destination_tag=4242)
    assert prepared["ok"] and prepared["destination_tag"] == 4242 and spy.calls == []
    stale = approval_code(
        SECRET, "xrp", "testnet", "hot", prepared["confirm_id"], prepared["address"], prepared["amount"], prepared["fee"]
    )
    assert not gate.send_payment("xrp", prepared["confirm_id"], stale)["ok"]
    assert spy.calls == []
    other = approval_code(
        SECRET, "xrp", "testnet", "hot", prepared["confirm_id"], prepared["address"],
        prepared["amount"], prepared["fee"], extra="tag:1",
    )
    assert not gate.send_payment("xrp", prepared["confirm_id"], other)["ok"]
    assert spy.calls == []
    code = approval_code(
        SECRET, "xrp", "testnet", "hot", prepared["confirm_id"], prepared["address"],
        prepared["amount"], prepared["fee"], extra=prepared["approval_extra"],
    )
    sent = gate.send_payment("xrp", prepared["confirm_id"], code)
    assert sent["ok"] and len(spy.calls) == 1
    from xrpl.core.binarycodec import decode
    decoded = decode(spy.calls[0])
    assert int(decoded["DestinationTag"]) == 4242
    assert decoded["Destination"] == KRAKEN


def test_xrp_signed_tag_must_match_the_approved_tag():
    wallet = Wallet.from_seed(XRP_SEED)
    adapter = XrpAdapter(
        network="testnet", role="hot", address=wallet.classic_address, rpc=XrpRpc(),
        max_fee_rate=100, seed=wallet.seed,
    )
    prepared = adapter.prepare("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", 1000, destination_tag=7)
    payload = json.loads(prepared.payload)
    payload["destination_tag"] = 8
    tampered = PreparedTransfer(
        prepared.address, prepared.amount, prepared.fee,
        json.dumps(payload, separators=(",", ":"), sort_keys=True), extra=prepared.extra,
    )
    spy = Spy()
    with pytest.raises(AdapterError, match="does not match"):
        adapter.sign(tampered, spy)
    assert spy.calls == []


def test_xrp_require_dest_flag_requires_a_tag():
    class Flagged(XrpRpc):
        def requires_destination_tag(self, address):
            return True

    wallet = Wallet.from_seed(XRP_SEED)
    adapter = XrpAdapter(
        network="testnet", role="hot", address=wallet.classic_address, rpc=Flagged(),
        max_fee_rate=100, seed=wallet.seed,
    )
    with pytest.raises(AdapterError, match="destination tag is required"):
        adapter.prepare("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", 10)
    prepared = adapter.prepare("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", 10, destination_tag=0)
    assert json.loads(prepared.payload)["destination_tag"] == 0


def prepared_from(body):
    return PreparedTransfer(body["address"], body["amount"], body["fee"], body["unsigned_payload"])


class LtcChain:
    def __init__(self, own_prefix):
        self.own_prefix = own_prefix

    def utxos_for(self, address, branch, index):
        if branch == 0 and index == 0 and address.startswith(self.own_prefix):
            return [Utxo("ab" * 32, 0, 80_000, 0, 0)]
        return []

    def fee_rate(self):
        return 1


def test_ltc_vector_watch_only_and_mocked_send(tmp_path):
    # Same BIP84 witness program as the Bitcoin test vector, Litecoin testnet HRP.
    assert export_xpub(MNEMONIC, "testnet").startswith("tpub")
    hot = LitecoinAdapter(
        network="testnet", role="hot", chain=LtcChain("tltc1q6rz"), max_fee_rate=50,
        index_store=MemoryIndex(), mnemonic=MNEMONIC,
    )
    assert hot.receive_address() == "tltc1q6rz28mcfaxtmd6v789l9rrlrusdprr9pesrjxk"
    main = LitecoinAdapter(
        network="mainnet", role="hot", chain=LtcChain("unused"), max_fee_rate=50,
        index_store=MemoryIndex(), mnemonic=MNEMONIC,
    )
    main_xpub = export_xpub(MNEMONIC, "mainnet")
    assert main_xpub == (
        "xpub6CjGURuDpczf6uNrCCwfhVizn5J3hsWcvZ2m6GAdmAjZnoWJPrx6TFPjGSftc2o5fvox6ubQjSXmjjaHZjwYMH7SGFpHHb9Jg24zBf66mbE"
    )
    assert main.receive_address() == "ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh"
    main_cold = LitecoinAdapter(
        network="mainnet", role="cold", chain=LtcChain("unused"), max_fee_rate=50,
        index_store=MemoryIndex(), xpub=main_xpub,
    )
    assert main_cold.receive_address() == "ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh"
    # A fresh watch-only index derives the same first address as the hot key.
    cold_again = LitecoinAdapter(
        network="testnet", role="cold", chain=LtcChain("unused"), max_fee_rate=50,
        index_store=MemoryIndex(), xpub=export_xpub(MNEMONIC, "testnet"),
    )
    assert cold_again.receive_address() == "tltc1q6rz28mcfaxtmd6v789l9rrlrusdprr9pesrjxk"

    destination = LitecoinAdapter(
        network="testnet", role="hot", chain=LtcChain("nope"), max_fee_rate=50,
        index_store=MemoryIndex(), mnemonic=OTHER,
    ).receive_address()
    spy = Spy()
    policy = Policy("ltc", "testnet", "litoshi", tmp_path, lambda: (50_000, 50_000, frozenset()))
    spender = LitecoinAdapter(
        network="testnet", role="hot", chain=LtcChain("tltc1q6rz"), max_fee_rate=50,
        index_store=MemoryIndex({"receive": 1, "change": 0}), mnemonic=MNEMONIC,
    )
    gate = PaymentGate({"ltc": spender}, {"ltc": policy}, {"ltc": spy}, lambda: SECRET)
    prepared = gate.prepare_payment("ltc", destination, 10_000)
    assert prepared["ok"] and spy.calls == []
    assert not gate.send_payment("ltc", prepared["confirm_id"], "")["ok"]
    assert spy.calls == []
    assert "per-tx" in gate.prepare_payment("ltc", destination, 60_000)["error"]
    code = approval_code(
        SECRET, "ltc", "testnet", "hot", prepared["confirm_id"], prepared["address"], prepared["amount"], prepared["fee"]
    )
    sent = gate.send_payment("ltc", prepared["confirm_id"], code)
    assert sent["ok"] and len(spy.calls) == 1
    with pytest.raises(AdapterError, match="cannot sign"):
        cold_again.sign(prepared_from(prepared), spy)
    assert len(spy.calls) == 1


def test_bitcoin_adapter_broadcasts_only_through_the_gate(tmp_path):
    class Wallet:
        def __init__(self):
            self.client = None

        def sync(self):
            return None

        def fee_rate(self):
            return 2

        def build(self, address, amount, rate):
            return "PSBT", 100

        def sign_and_broadcast(self, psbt, address, amount, fee):
            self.client.broadcast("signed-tx")
            return "txid-btc"

    adapter = BitcoinAdapter(Wallet(), network="signet", role="hot")
    spy = Spy()
    policy = Policy("btc", "signet", "sat", tmp_path, lambda: (10_000, 10_000, frozenset()))
    gate = PaymentGate({"btc": adapter}, {"btc": policy}, {"btc": spy}, lambda: SECRET)
    prepared = gate.prepare_payment("btc", "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad", 1000)
    assert prepared["ok"] and prepared["unsigned_psbt"] == "PSBT" and spy.calls == []
    assert not gate.send_payment("btc", prepared["confirm_id"], "")["ok"]
    assert spy.calls == []
    code = approval_code(
        SECRET, "btc", "signet", "hot", prepared["confirm_id"], prepared["address"], prepared["amount"], prepared["fee"]
    )
    sent = gate.send_payment("btc", prepared["confirm_id"], code)
    assert sent["ok"] and spy.calls == ["signed-tx"]


def test_adapters_do_not_replace_execute():
    for cls in (BitcoinAdapter, EvmAdapter, XrpAdapter, LitecoinAdapter):
        assert "execute" not in cls.__dict__


def test_watch_import_rejects_secrets(tmp_path):
    from wallet_mcp.watch import assert_watch_only
    from wallet_mcp.config import ConfigError
    with pytest.raises(ConfigError):
        assert_watch_only({"external": "tprv8ZgxMBicQKsPe5YMU9gHen4Ez3ApihUfykaqUorj9t6FDqy3nP6eoXiAo2ssvpAjoLroQxHqr3R5nE3a5dU3DHTjTgJDd7zrbniJr6nrCzd"})
    with pytest.raises(ConfigError):
        assert_watch_only({"mnemonic": MNEMONIC})
    with pytest.raises(ConfigError):
        assert_watch_only({"address": XRP_SEED})
    descriptors = public_descriptors(MNEMONIC, "signet")
    assert_watch_only(descriptors)  # public descriptor is accepted
