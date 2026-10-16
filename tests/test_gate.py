"""The central gate is mandatory: no approval or an over-limit payment never broadcasts."""
import pytest

from wallet_mcp.adapters.base import AdapterError, ChainAdapter, PreparedTransfer
from wallet_mcp.auth import AuthError, SpendAuthorization
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, approval_code

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


class Fake(ChainAdapter):
    unit = "sat"
    experimental = False
    supports_transactions = False

    def __init__(self, coin, network, role="hot"):
        self.coin_id = coin
        self.network = network
        self.role = role
        self.can_sign = role == "hot"
        self.signed = 0

    def prepare(self, address, amount, memo="", destination_tag=None):
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        if address == "bad":
            raise AdapterError("invalid address for this network")
        return PreparedTransfer(address, amount, 150, "UNSIGNED", fee_rate=2)

    def sign(self, prepared, broadcaster):
        self.signed += 1
        broadcaster(b"raw")
        return "txid1"


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "txid1"


def build(tmp_path, coins=("btc",), limits=None):
    limits = limits or {"btc": (10_000, 15_000), "eth": (10_000, 15_000)}
    clock = Clock()
    adapters, policies, spies = {}, {}, {}
    for coin in coins:
        network = "signet" if coin == "btc" else "sepolia"
        adapters[coin] = Fake(coin, network)
        spies[coin] = Spy()
        max_tx, max_day = limits[coin]
        policies[coin] = Policy(
            coin, network, "sat" if coin == "btc" else "wei", tmp_path / coin,
            lambda mx=max_tx, md=max_day: (mx, md, frozenset()), now=clock,
        )
    gate = PaymentGate(adapters, policies, spies, lambda: SECRET, {coin: 600 for coin in coins})
    gate.clock = clock
    gate.spies = spies
    return gate


def code(gate, coin, body):
    network = "signet" if coin == "btc" else "sepolia"
    return approval_code(SECRET, coin, network, "hot", body["confirm_id"], body["address"], body["amount"], body["fee"])


def test_happy_path_broadcasts_once(tmp_path):
    gate = build(tmp_path)
    prepared = gate.prepare_payment("btc", ADDR, 5000, "coffee")
    assert prepared["ok"] and prepared["unsigned_psbt"] == "UNSIGNED"
    assert gate.spies["btc"].calls == []
    result = gate.send_payment("btc", prepared["confirm_id"], code(gate, "btc", prepared))
    assert result["ok"] and result["txid"] == "txid1"
    assert gate.spies["btc"].calls == [b"raw"]
    assert gate.policies["btc"].spent_last_24h() == 5150
    assert not gate.send_payment("btc", prepared["confirm_id"], code(gate, "btc", prepared))["ok"]
    assert gate.spies["btc"].calls == [b"raw"]


def test_send_without_or_with_wrong_code_does_not_broadcast(tmp_path):
    gate = build(tmp_path)
    prepared = gate.prepare_payment("btc", ADDR, 5000)
    assert "invalid" in gate.send_payment("btc", prepared["confirm_id"], "")["error"]
    assert "invalid" in gate.send_payment("btc", prepared["confirm_id"], "000000000000")["error"]
    assert gate.spies["btc"].calls == []
    assert gate.adapters["btc"].signed == 0


def test_code_does_not_cross_coins(tmp_path):
    gate = build(tmp_path, coins=("btc", "eth"))
    btc = gate.prepare_payment("btc", ADDR, 5000)
    eth = gate.prepare_payment("eth", "0xabc", 5000)
    refused = gate.send_payment("eth", eth["confirm_id"], code(gate, "btc", btc))
    assert not refused["ok"]
    assert gate.spies["eth"].calls == [] and gate.spies["btc"].calls == []


def test_over_limit_does_not_broadcast(tmp_path):
    gate = build(tmp_path)
    assert "per-tx" in gate.prepare_payment("btc", ADDR, 10_001)["error"]
    assert "per-tx" in gate.prepare_payment("btc", ADDR, 9900)["error"]  # + 150 fee
    assert gate.spies["btc"].calls == []
    assert gate.prepare_payment("btc", ADDR, 9850)["ok"]


def test_daily_limit_blocks_prepare_and_send(tmp_path):
    gate = build(tmp_path)
    first = gate.prepare_payment("btc", ADDR, 9000)
    assert gate.send_payment("btc", first["confirm_id"], code(gate, "btc", first))["ok"]
    assert "daily" in gate.prepare_payment("btc", ADDR, 6000)["error"]
    gate.clock.t += 86_401
    assert gate.prepare_payment("btc", ADDR, 6000)["ok"]


def test_daily_limit_rechecked_at_send(tmp_path):
    gate = build(tmp_path)
    first = gate.prepare_payment("btc", ADDR, 8000)
    second = gate.prepare_payment("btc", ADDR, 8000)
    assert gate.send_payment("btc", first["confirm_id"], code(gate, "btc", first))["ok"]
    refused = gate.send_payment("btc", second["confirm_id"], code(gate, "btc", second))
    assert "daily" in refused["error"]
    assert len(gate.spies["btc"].calls) == 1


def test_no_secret_disables_sending(tmp_path):
    gate = build(tmp_path)
    gate._secret = lambda: None
    prepared = gate.prepare_payment("btc", ADDR, 100)
    assert "disabled" in gate.send_payment("btc", prepared["confirm_id"], "abc")["error"]
    assert gate.spies["btc"].calls == []


def test_cold_adapter_cannot_spend(tmp_path):
    cold = Fake("btc", "signet", role="cold")
    policy = Policy("btc", "signet", "sat", tmp_path, lambda: (10_000, 15_000, frozenset()))
    spy = Spy()
    gate = PaymentGate({"btc": cold}, {"btc": policy}, {"btc": spy}, lambda: SECRET)
    refused = gate.prepare_payment("btc", ADDR, 100)
    assert not refused["ok"] and "cold" in refused["error"]
    assert spy.calls == [] and cold.signed == 0


def test_authorization_cannot_be_forged():
    with pytest.raises(AuthError):
        SpendAuthorization(
            coin="btc", network="signet", role="hot", confirm_id="pay_x",
            address=ADDR, amount=1, fee=1, seal=object(),
        )


@pytest.mark.parametrize("amount", [0, -5, True, "100"])
def test_bad_amounts(tmp_path, amount):
    assert not build(tmp_path).prepare_payment("btc", ADDR, amount)["ok"]
