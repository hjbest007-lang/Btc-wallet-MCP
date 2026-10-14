"""Service + policy tests with a fake wallet (no network)."""
import pytest
from btc_wallet_mcp.config import load_config
from btc_wallet_mcp.policy import Policy, approval_code
from btc_wallet_mcp.service import Service
from btc_wallet_mcp.wallet import WalletError

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


class FakeWallet:
    def __init__(self): self.sent = []; self.fee = 150
    def sync(self): pass
    def new_address(self): return "tb1qnew"
    def balance(self): return {"total_sats": 1}
    def transactions(self, n): return []
    def fee_rate(self): return 2
    def build(self, a, amt, rate):
        if a == "bad": raise WalletError("invalid address for this network")
        return "PSBT", self.fee
    def sign_and_broadcast(self, psbt, a, amt, fee): self.sent.append((a, amt)); return "txid1"


class Clock:
    t = 1_000_000.0
    def __call__(self): return self.t


@pytest.fixture
def svc(tmp_path):
    def make(**env):
        e = {"BTC_WALLET_DATA_DIR": str(tmp_path), "BTC_WALLET_MAX_SATS_PER_TX": "10000",
             "BTC_WALLET_MAX_SATS_PER_DAY": "15000", **env}
        cfg = load_config(e)
        clock = Clock()
        s = Service(cfg, FakeWallet(), Policy(cfg, now=clock), env={"BTC_WALLET_APPROVAL_SECRET": SECRET})
        s.clock = clock
        return s
    return make


def code(p): return approval_code(SECRET, p["confirm_id"], p["address"], p["amount_sats"], p["fee_sats"])


def test_happy_path(svc):
    s = svc()
    p = s.prepare_payment(ADDR, 5000, "coffee")
    assert p["ok"] and p["fee_sats"] == 150 and p["unsigned_psbt"] == "PSBT"
    r = s.send_payment(p["confirm_id"], code(p))
    assert r == {"ok": True, "txid": "txid1", "amount_sats": 5000, "fee_sats": 150}
    assert s.policy.spent_last_24h() == 5150
    # single use
    assert not s.send_payment(p["confirm_id"], code(p))["ok"]


def test_send_without_code_or_wrong_code(svc):
    s = svc()
    p = s.prepare_payment(ADDR, 5000)
    assert "invalid" in s.send_payment(p["confirm_id"], "")["error"]
    assert "invalid" in s.send_payment(p["confirm_id"], "000000000000")["error"]
    assert s.wallet.sent == []


def test_code_bound_to_payment(svc):
    s = svc()
    p1 = s.prepare_payment(ADDR, 5000); p2 = s.prepare_payment(ADDR, 6000)
    assert not s.send_payment(p2["confirm_id"], code(p1))["ok"]


def test_lockout(svc):
    s = svc()
    p = s.prepare_payment(ADDR, 100)
    for _ in range(5):
        s.send_payment(p["confirm_id"], "x")
    r = s.send_payment(p["confirm_id"], code(p))
    assert not r["ok"] and "locked" in r["error"]
    s.policy.unlock()
    assert s.send_payment(p["confirm_id"], code(p))["ok"]


def test_no_secret_disables_sending(svc, tmp_path):
    s = svc(); s._env = {}
    p = s.prepare_payment(ADDR, 100)
    assert "disabled" in s.send_payment(p["confirm_id"], "abc")["error"]
    assert s.get_config()["sending_enabled"] is False


def test_per_tx_limit_incl_fee(svc):
    s = svc()
    assert not s.prepare_payment(ADDR, 10001)["ok"]
    assert not s.prepare_payment(ADDR, 9900)["ok"]  # + 150 fee > 10000
    assert s.prepare_payment(ADDR, 9850)["ok"]


def test_daily_limit_rolling(svc):
    s = svc()
    p = s.prepare_payment(ADDR, 9000); assert s.send_payment(p["confirm_id"], code(p))["ok"]
    assert "daily" in s.prepare_payment(ADDR, 6000)["error"]
    s.clock.t += 86_401
    assert s.prepare_payment(ADDR, 6000)["ok"]


def test_daily_limit_rechecked_at_send(svc):
    s = svc()
    a = s.prepare_payment(ADDR, 8000); b = s.prepare_payment(ADDR, 8000)
    assert s.send_payment(a["confirm_id"], code(a))["ok"]
    assert "daily" in s.send_payment(b["confirm_id"], code(b))["error"]


def test_expiry(svc):
    s = svc()
    p = s.prepare_payment(ADDR, 100)
    s.clock.t += 601
    assert "expired" in s.send_payment(p["confirm_id"], code(p))["error"]


def test_allowlist(svc):
    s = svc(BTC_WALLET_ALLOWLIST=ADDR)
    assert s.prepare_payment(ADDR, 100)["ok"]
    assert "allowlist" in s.prepare_payment("tb1qother", 100)["error"]


@pytest.mark.parametrize("amt", [0, -5, 1.5, True, "100"])
def test_bad_amounts(svc, amt):
    assert not svc().prepare_payment(ADDR, amt)["ok"]


def test_no_secrets_in_outputs(svc):
    s = svc()
    out = str(s.get_config()) + str(s.prepare_payment(ADDR, 100)) + str(s.get_address())
    assert SECRET not in out
