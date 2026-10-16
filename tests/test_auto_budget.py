"""Owner-granted autonomous budget. Default off. Base units. No price oracle.

A payment that fits auto_max_per_tx and the remaining auto_max_per_day is
broadcast from prepare and does not need an approval code. It still counts
toward the hard daily limit. Anything above the budget falls back to
wallet-mcp approve. Cold wallets never take the path. The agent has no tool
that can raise the budget, and a config edit that breaks auto_mac fails closed.
"""
import json
import threading

import pytest

from wallet_mcp.adapters.base import AdapterError, ChainAdapter, PreparedTransfer, UnconfiguredCold, UnconfiguredHot
from wallet_mcp.audit import AuditLog, WebhookNotifier
from wallet_mcp.config import (
    ConfigError,
    load_config,
    propose_auto_budget,
    propose_limits,
    save_config,
)
from wallet_mcp.configure import apply_form
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, PolicyError, approval_code, auto_approval_code, auto_mac, limit_approval_code
from wallet_mcp.registry import Bundle, build_runtime, tool_names
from wallet_mcp.server import make_app

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


def env(tmp_path, **extra):
    base = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    base.update(extra)
    return base


class Fake(ChainAdapter):
    unit = "sat"
    experimental = False
    supports_transactions = False

    def __init__(self, coin="btc", network="signet", role="hot", fee=150, extra=""):
        self.coin_id = coin
        self.network = network
        self.role = role
        self.can_sign = role == "hot"
        self.fee = fee
        self.extra = extra
        self.signed = 0

    def prepare(self, address, amount, memo="", destination_tag=None):
        if destination_tag is not None:
            raise AdapterError("destination tag is not used on this coin")
        if address == "bad":
            raise AdapterError("invalid address for this network")
        return PreparedTransfer(address, amount, self.fee, "UNSIGNED", extra=self.extra)

    def sign(self, prepared, broadcaster):
        self.signed += 1
        broadcaster(b"raw")
        return "txid-auto"


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "txid-auto"


def grant(tmp_path, coin, tx, day):
    cfg = load_config(env(tmp_path))
    status, request = propose_auto_budget(cfg, coin, tx, day)
    assert status == "pending" and request is not None
    assert load_config(env(tmp_path)).coins[coin].auto_max_per_tx == 0
    code = auto_approval_code(SECRET, request.request_id, coin, tx, day)
    from wallet_mcp.config import approve_auto_budget
    approve_auto_budget(load_config(env(tmp_path)), request.request_id, code, SECRET)
    loaded = load_config(env(tmp_path))
    assert loaded.coins[coin].auto_max_per_tx == tx
    assert loaded.coins[coin].auto_max_per_day == day
    assert loaded.coins[coin].auto_mac == auto_mac(SECRET, coin, tx, day)
    return loaded


def _providers(tmp_path, coin, allow=frozenset()):
    def limits():
        try:
            fresh = load_config(env(tmp_path)).coins[coin]
        except ConfigError as exc:
            raise PolicyError(str(exc)) from None
        return fresh.max_per_tx, fresh.max_per_day, allow

    def autos():
        try:
            fresh = load_config(env(tmp_path)).coins[coin]
        except ConfigError as exc:
            raise PolicyError(str(exc)) from None
        return fresh.auto_max_per_tx, fresh.auto_max_per_day

    return limits, autos


def make_gate(tmp_path, coins=None, allow=frozenset(), fee=150, role="hot", extra="", parent=None, notifier=None):
    """One hot coin, re-reading the on-disk budget the way the server does."""
    coins = coins or ["btc"]
    adapters, policies, spies = {}, {}, {}
    for coin in coins:
        network = "signet" if coin == "btc" else "sepolia"
        adapter = Fake(coin, network, role=role, fee=fee, extra=extra if coin == coins[0] else "")
        if parent and coin == coins[0]:
            adapter.parent_fee_coin = parent
        adapters[coin] = adapter
        spies[coin] = Spy()
        limits, autos = _providers(tmp_path, coin, allow=allow if coin == coins[0] else frozenset())
        policies[coin] = Policy(
            coin, network, "sat", tmp_path / coin, limits,
            secret_provider=lambda: SECRET, auto_limits=autos,
        )
    audit = AuditLog(tmp_path / "audit.log")
    gate = PaymentGate(
        adapters, policies, spies, lambda: SECRET, {coin: 600 for coin in coins},
        auditor=audit, notifier=notifier,
    )
    gate.spies = spies
    gate.audit = audit
    return gate


def test_default_budget_is_off_and_prepare_does_not_send(tmp_path):
    cfg = load_config(env(tmp_path))
    assert cfg.coins["btc"].auto_max_per_tx == 0
    assert cfg.coins["btc"].auto_max_per_day == 0
    assert cfg.coins["btc"].auto_mac == ""
    # A file written before these fields existed still loads as off.
    raw = json.loads((tmp_path / "config.json").read_text()) if (tmp_path / "config.json").exists() else None
    if raw is None:
        save_config(cfg)
        raw = json.loads((tmp_path / "config.json").read_text())
    for slot in raw["coins"].values():
        slot.pop("auto_max_per_tx", None)
        slot.pop("auto_max_per_day", None)
        slot.pop("auto_mac", None)
    (tmp_path / "config.json").write_text(json.dumps(raw))
    again = load_config(env(tmp_path))
    assert again.coins["eth"].auto_max_per_tx == 0 and again.coins["eth"].auto_max_per_day == 0

    gate = make_gate(tmp_path)
    prepared = gate.prepare_payment("btc", ADDR, 1000)
    assert prepared["ok"] and prepared["confirm_id"].startswith("pay_")
    assert "auto" not in prepared
    assert "approve" in prepared["next_step"]
    assert gate.spies["btc"].calls == []
    assert gate.adapters["btc"].signed == 0


def test_within_budget_sends_without_approval_code(tmp_path):
    grant(tmp_path, "btc", 5_000, 10_000)
    seen = []
    notifier = WebhookNotifier("https://hooks.example/wallet", poster=lambda url, payload: seen.append(payload))
    gate = make_gate(tmp_path, fee=150, notifier=notifier)
    result = gate.prepare_payment("btc", ADDR, 800, "coffee")
    assert result["ok"] is True and result["auto"] is True
    assert result["txid"] == "txid-auto"
    assert "next_step" not in result
    assert gate.spies["btc"].calls == [b"raw"]
    assert gate.adapters["btc"].signed == 1
    assert gate.policies["btc"].auto_spent_last_24h() == 950
    assert gate.policies["btc"].spent_last_24h() == 950
    text = (tmp_path / "audit.log").read_text()
    assert '"auto": true' in text and "send_ok" in text
    assert SECRET not in text
    assert seen and seen[0]["event"] == "auto_spent" and seen[0]["auto"] is True
    assert seen[0]["txid"] == "txid-auto" and "approval_code" not in seen[0]
    assert SECRET not in json.dumps(seen[0])
    # The confirm was consumed. A later send without a code does not broadcast again.
    refused = gate.send_payment("btc", result["confirm_id"], "")
    assert not refused["ok"]
    assert gate.spies["btc"].calls == [b"raw"]


def test_webhook_failure_does_not_block_an_auto_send(tmp_path):
    grant(tmp_path, "btc", 5_000, 10_000)

    def explode(url, payload):
        raise RuntimeError("down")

    gate = make_gate(tmp_path, notifier=WebhookNotifier("https://hooks.example/wallet", poster=explode))
    result = gate.prepare_payment("btc", ADDR, 100)
    assert result["ok"] and result["auto"] is True and result["txid"] == "txid-auto"
    assert "webhook_fail" in (tmp_path / "audit.log").read_text()


def test_over_per_tx_or_per_day_falls_back_to_approval(tmp_path):
    grant(tmp_path, "btc", 1_000, 2_000)
    seen = []
    gate = make_gate(
        tmp_path, fee=150,
        notifier=WebhookNotifier("https://hooks.example/wallet", poster=lambda _u, payload: seen.append(payload)),
    )
    # 900 + 150 fee = 1050, over the 1000 per-tx budget, inside the hard cap.
    over_tx = gate.prepare_payment("btc", ADDR, 900)
    assert over_tx["ok"] and "auto" not in over_tx and "approve" in over_tx["next_step"]
    assert gate.spies["btc"].calls == []
    assert seen and seen[0]["event"] == "approval_requested"
    code = approval_code(
        SECRET, "btc", "signet", "hot", over_tx["confirm_id"], ADDR, over_tx["amount"], over_tx["fee"],
    )
    assert gate.send_payment("btc", over_tx["confirm_id"], code)["ok"]
    assert len(gate.spies["btc"].calls) == 1

    day = tmp_path / "day"
    grant(day, "btc", 1_000, 1_000)
    gate2 = make_gate(day, fee=150)
    # 800 + 150 = 950, inside the per-tx budget. A second 950 does not fit the day (950 + 950 > 1000).
    first = gate2.prepare_payment("btc", ADDR, 800)
    assert first["auto"] is True and len(gate2.spies["btc"].calls) == 1
    second = gate2.prepare_payment("btc", ADDR, 800)
    assert second["ok"] and "auto" not in second and "approve" in second["next_step"]
    assert len(gate2.spies["btc"].calls) == 1
    assert gate2.policies["btc"].auto_spent_last_24h() == 950
    assert gate2.policies["btc"].spent_last_24h() == 950


def test_auto_spend_counts_toward_the_hard_daily_limit(tmp_path):
    cfg = load_config(env(tmp_path))
    status, _request = propose_limits(cfg, "btc", 50_000, 1_200)
    assert status == "applied"
    # The auto day cannot exceed the hard day. Filling it must also fill the hard counter.
    grant(tmp_path, "btc", 1_000, 1_200)
    gate = make_gate(tmp_path, fee=100)
    first = gate.prepare_payment("btc", ADDR, 500)  # total 600
    second = gate.prepare_payment("btc", ADDR, 500)  # total 600; 1200 is still inside both caps
    assert first["auto"] is True and second["auto"] is True
    third = gate.prepare_payment("btc", ADDR, 100)  # +100 fee = 200; 1200 + 200 > 1200
    assert not third["ok"] and "daily" in third["error"]
    assert len(gate.spies["btc"].calls) == 2


def _wipe(tmp_path):
    for name in ("config.json", "auto-requests.json", "limit-requests.json"):
        path = tmp_path / name
        if path.exists():
            path.unlink()


def test_tampered_auto_mac_fails_closed(tmp_path):
    grant(tmp_path, "btc", 5_000, 10_000)
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"]["btc"]["auto_max_per_tx"] = 40_000
    cfg.raw["coins"]["btc"]["auto_max_per_day"] = 40_000
    cfg.raw["coins"]["btc"]["auto_mac"] = "deadbeef"
    save_config(cfg)
    with pytest.raises(ConfigError, match="not approved"):
        load_config(env(tmp_path))

    # A limit-approval code is not an auto MAC.
    _wipe(tmp_path)
    grant(tmp_path, "btc", 5_000, 10_000)
    valid = load_config(env(tmp_path))
    valid.raw["coins"]["btc"]["auto_mac"] = limit_approval_code(SECRET, "lim_x", "btc", 5_000, 10_000)
    save_config(valid)
    with pytest.raises(ConfigError, match="not approved"):
        load_config(env(tmp_path))

    # Another coin's MAC does not authorize this coin.
    _wipe(tmp_path)
    grant(tmp_path, "btc", 5_000, 10_000)
    stolen = load_config(env(tmp_path))
    stolen.raw["coins"]["eth"]["auto_max_per_tx"] = 5_000
    stolen.raw["coins"]["eth"]["auto_max_per_day"] = 10_000
    stolen.raw["coins"]["eth"]["auto_mac"] = stolen.raw["coins"]["btc"]["auto_mac"]
    save_config(stolen)
    with pytest.raises(ConfigError, match="not approved"):
        load_config(env(tmp_path))

    # A MAC over a larger pair cannot lift the budget above the hard limits.
    _wipe(tmp_path)
    grant(tmp_path, "btc", 5_000, 10_000)
    over = load_config(env(tmp_path))
    over.raw["coins"]["btc"]["auto_max_per_tx"] = 60_000
    over.raw["coins"]["btc"]["auto_max_per_day"] = 60_000
    over.raw["coins"]["btc"]["auto_mac"] = auto_mac(SECRET, "btc", 60_000, 60_000)
    save_config(over)
    with pytest.raises(ConfigError, match="exceeds"):
        load_config(env(tmp_path))

    _wipe(tmp_path)
    grant(tmp_path, "btc", 5_000, 10_000)
    gate = make_gate(tmp_path)
    broken = load_config(env(tmp_path))
    broken.raw["coins"]["btc"]["auto_mac"] = "00" * 32
    save_config(broken)
    refused = gate.prepare_payment("btc", ADDR, 100)
    assert not refused["ok"]
    assert "not approved" in refused["error"]
    assert gate.spies["btc"].calls == []
    assert gate.adapters["btc"].signed == 0


def test_concurrent_prepares_cannot_exceed_the_auto_budget(tmp_path, monkeypatch):
    grant(tmp_path, "btc", 3_000, 3_000)
    gate = make_gate(tmp_path, fee=150)
    barrier = threading.Barrier(2)
    real = Policy.add_pending

    def wait_then_continue(self, *args, **kwargs):
        pending = real(self, *args, **kwargs)
        barrier.wait(timeout=5)
        return pending

    monkeypatch.setattr(Policy, "add_pending", wait_then_continue)
    results = []

    def once():
        results.append(gate.prepare_payment("btc", ADDR, 2_000))

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    autos = [item for item in results if item.get("auto")]
    waiting = [item for item in results if item.get("ok") and not item.get("auto")]
    assert len(autos) == 1 and autos[0]["txid"] == "txid-auto"
    assert len(waiting) == 1 and "approve" in waiting[0]["next_step"]
    assert len(gate.spies["btc"].calls) == 1
    assert gate.policies["btc"].auto_spent_last_24h() == 2_150
    assert gate.policies["btc"].spent_last_24h() == 2_150


def test_agent_cannot_raise_the_budget(tmp_path):
    granted = grant(tmp_path, "btc", 1_000, 2_000)
    # Queuing an increase does not change the spendable numbers, even though the
    # server process holds the approval secret.
    status, request = propose_auto_budget(load_config(env(tmp_path)), "btc", 4_000, 8_000)
    assert status == "pending"
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 1_000
    from wallet_mcp.config import approve_auto_budget
    with pytest.raises(ConfigError, match="invalid"):
        approve_auto_budget(load_config(env(tmp_path)), request.request_id, "000000000000", SECRET)
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 1_000
    # A limit-approval code is not an auto-budget code.
    wrong = limit_approval_code(SECRET, request.request_id, "btc", 4_000, 8_000)
    with pytest.raises(ConfigError, match="invalid"):
        approve_auto_budget(load_config(env(tmp_path)), request.request_id, wrong, SECRET)
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_day == 2_000

    # The configure page has no field that sticks, and no MCP tool writes the budget.
    notes = apply_form(load_config(env(tmp_path)), {
        "coin_btc": ["on"],
        "tx_btc": ["50000"],
        "day_btc": ["100000"],
        "auto_max_per_tx": ["999999"],
        "auto_max_per_day": ["999999"],
        "auto_tx_btc": ["999999"],
        "auto_day_btc": ["999999"],
    })
    assert "999999" not in notes
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 1_000
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_day == 2_000

    from wallet_mcp.config import apply_enabled
    apply_enabled(load_config(env(tmp_path)), {"btc"})
    cfg = load_config(env(tmp_path))

    def factory(coin_id):
        def build(_cfg):
            hot = UnconfiguredHot(coin_id, "signet", "sat", False)
            cold = UnconfiguredCold(coin_id, "signet", "sat", False)
            return Bundle(hot, cold, lambda _payload: (_ for _ in ()).throw(AssertionError("broadcast")))
        return build

    runtime = build_runtime(cfg, {"btc": factory("btc")})
    names = tool_names(runtime)
    assert "btc_prepare_payment" in names and "btc_send_payment" in names
    for name in names:
        assert "auto" not in name and "budget" not in name and "limit" not in name
    app = make_app(runtime)
    registered = {tool.name for tool in app._tool_manager.list_tools()}
    assert registered == set(names)
    # The page the owner sees does not offer an input for the budget.
    from wallet_mcp.configure import render_page
    page = render_page(cfg, "tok")
    assert "auto_max_per_tx" in page  # explained as not editable here
    assert 'name="auto' not in page
    assert granted.coins["btc"].auto_mac  # sealed; the tools above cannot replace it


def test_approve_auto_requires_a_tty(tmp_path, monkeypatch):
    cfg = load_config(env(tmp_path))
    _status, request = propose_auto_budget(cfg, "btc", 1_000, 2_000)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    from wallet_mcp.cli import cmd_approve_auto
    import argparse
    with pytest.raises(SystemExit, match="interactive terminal"):
        cmd_approve_auto(load_config(env(tmp_path)), argparse.Namespace(request_id=request.request_id))
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 0


def test_owner_cli_sets_and_lowers_the_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WALLET_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WALLET_MCP_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("WALLET_MCP_APPROVAL_SECRET", SECRET)
    from wallet_mcp.cli import main
    main(["request-auto", "--coin", "btc", "--auto-max-per-tx", "10000", "--auto-max-per-day", "50000"])
    pending_line = capsys.readouterr().out.strip()
    request_id = pending_line.split()[1].rstrip(":")
    assert request_id.startswith("aut_")
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 0

    monkeypatch.setattr("wallet_mcp.cli._tty", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    main(["approve-auto", request_id])
    assert "autonomous budget updated" in capsys.readouterr().out
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 10_000

    # Lowering applies in the same command. No second approval.
    main(["request-auto", "--coin", "btc", "--auto-max-per-tx", "1000", "--auto-max-per-day", "2000"])
    assert capsys.readouterr().out.strip() == "applied"
    lowered = load_config(env(tmp_path)).coins["btc"]
    assert lowered.auto_max_per_tx == 1_000 and lowered.auto_max_per_day == 2_000
    assert lowered.auto_mac == auto_mac(SECRET, "btc", 1_000, 2_000)

    # Above the hard limit is refused, and the owner is told to raise that limit first.
    with pytest.raises(SystemExit, match="request-limit"):
        main(["request-auto", "--coin", "btc", "--auto-max-per-tx", "1000", "--auto-max-per-day", "500000"])
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_day == 2_000


def test_lowering_limits_clamps_the_auto_budget(tmp_path):
    grant(tmp_path, "btc", 1_000, 2_000)
    cfg = load_config(env(tmp_path))
    status, _request = propose_limits(cfg, "btc", 500, 800)
    assert status == "applied"
    clamped = load_config(env(tmp_path)).coins["btc"]
    assert clamped.max_per_tx == 500 and clamped.max_per_day == 800
    assert clamped.auto_max_per_tx == 500 and clamped.auto_max_per_day == 800
    assert clamped.auto_mac == auto_mac(SECRET, "btc", 500, 800)


def test_cold_wallet_never_auto_spends(tmp_path):
    grant(tmp_path, "btc", 50_000, 100_000)
    gate = make_gate(tmp_path, role="cold")
    refused = gate.prepare_payment("btc", ADDR, 100)
    assert not refused["ok"] and "cold" in refused["error"]
    assert gate.spies["btc"].calls == [] and gate.adapters["btc"].signed == 0


def test_allowlist_fee_cap_and_memo_still_apply(tmp_path):
    grant(tmp_path, "btc", 50_000, 100_000)
    gate = make_gate(tmp_path, allow=frozenset({"tb1qallowed"}))
    refused = gate.prepare_payment("btc", ADDR, 100)
    assert "allowlist" in refused["error"]
    assert gate.spies["btc"].calls == []

    dirty = make_gate(tmp_path, fee=150)
    assert "unsupported" in dirty.prepare_payment("btc", ADDR, 100, "ok\nTo: other")["error"]
    assert dirty.spies["btc"].calls == []
    assert "invalid address" in dirty.prepare_payment("btc", ADDR[:6] + "\n" + ADDR[6:], 100)["error"]

    class OverFee(Fake):
        def prepare(self, address, amount, memo="", destination_tag=None):
            raise AdapterError("fee exceeds max_fee_rate")

    over_fee = OverFee()
    limits, autos = _providers(tmp_path, "btc")
    policy = Policy(
        "btc", "signet", "sat", tmp_path / "fee-policy", limits,
        secret_provider=lambda: SECRET, auto_limits=autos,
    )
    spy = Spy()
    capped = PaymentGate({"btc": over_fee}, {"btc": policy}, {"btc": spy}, lambda: SECRET)
    assert "max_fee_rate" in capped.prepare_payment("btc", ADDR, 100)["error"]
    assert spy.calls == [] and over_fee.signed == 0

    # The per-tx budget is amount plus fee, same as the hard cap. 900+150 exceeds 1000.
    tight = make_gate(tmp_path / "tight", fee=150)
    grant(tmp_path / "tight", "btc", 1_000, 5_000)
    over = tight.prepare_payment("btc", ADDR, 900)
    assert over["ok"] and "auto" not in over and tight.spies["btc"].calls == []
    exact = tight.prepare_payment("btc", ADDR, 850)  # 850 + 150 = 1000
    assert exact["auto"] is True and len(tight.spies["btc"].calls) == 1


def test_mainnet_opt_in_still_required(tmp_path):
    grant(tmp_path, "btc", 1_000, 2_000)
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"]["btc"]["network"] = "mainnet"
    cfg.raw["coins"]["btc"]["enabled"] = True
    save_config(cfg)
    with pytest.raises(ConfigError, match="mainnet"):
        load_config(env(tmp_path))


def test_token_auto_spend_also_reserves_parent_gas(tmp_path):
    grant(tmp_path, "usdc_eth", 5_000_000, 5_000_000)
    # Parent auto budget is still 0, so the token payment must not auto-send:
    # the gas would be native ETH spent without an approval.
    gate = make_gate(tmp_path, coins=["usdc_eth", "eth"], fee=0, extra="gas:100", parent="eth")
    waiting = gate.prepare_payment("usdc_eth", "0x0000000000000000000000000000000000000001", 1_000)
    assert waiting["ok"] and "auto" not in waiting and "approve" in waiting["next_step"]
    assert gate.spies["usdc_eth"].calls == []

    grant(tmp_path, "eth", 1_000, 1_000)
    sent = gate.prepare_payment("usdc_eth", "0x0000000000000000000000000000000000000001", 1_000)
    assert sent["auto"] is True and sent["txid"] == "txid-auto"
    assert gate.spies["usdc_eth"].calls == [b"raw"]
    assert gate.policies["usdc_eth"].auto_spent_last_24h() == 1_000
    assert gate.policies["eth"].auto_spent_last_24h() == 100
    assert gate.policies["eth"].spent_last_24h() == 100


def test_auto_request_expires(tmp_path):
    cfg = load_config(env(tmp_path))
    status, request = propose_auto_budget(cfg, "btc", 1_000, 2_000, now=lambda: 1.0)
    assert status == "pending"
    from wallet_mcp.config import approve_auto_budget
    with pytest.raises(ConfigError, match="expired"):
        approve_auto_budget(load_config(env(tmp_path)), request.request_id, "000000000000", SECRET)
    assert load_config(env(tmp_path)).coins["btc"].auto_max_per_tx == 0


def test_lowering_to_zero_clears_the_mac_without_a_new_approval(tmp_path):
    grant(tmp_path, "btc", 1_000, 2_000)
    cfg = load_config(env(tmp_path))
    cfg.env["WALLET_MCP_APPROVAL_SECRET"] = ""
    status, request = propose_auto_budget(cfg, "btc", 0, 0)
    assert status == "applied" and request is None
    # Reload with the secret so verification can run; zeros need no MAC.
    off = load_config(env(tmp_path)).coins["btc"]
    assert off.auto_max_per_tx == 0 and off.auto_max_per_day == 0 and off.auto_mac == ""
    gate = make_gate(tmp_path)
    prepared = gate.prepare_payment("btc", ADDR, 100)
    assert prepared["ok"] and "auto" not in prepared
    assert gate.spies["btc"].calls == []
