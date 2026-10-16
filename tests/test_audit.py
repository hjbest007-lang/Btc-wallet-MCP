"""Audit log and the approval webhook. Secrets never land in either."""
import json

import pytest

from wallet_mcp.adapters.base import AdapterError, ChainAdapter, PreparedTransfer
from wallet_mcp.audit import AuditLog, WebhookNotifier
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, approval_code

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


class Fake(ChainAdapter):
    unit = "sat"
    experimental = False

    def __init__(self):
        self.coin_id = "btc"
        self.network = "signet"
        self.role = "hot"
        self.can_sign = True

    def prepare(self, address, amount, memo="", destination_tag=None):
        if address == "bad":
            raise AdapterError("invalid address for this network")
        return PreparedTransfer(address, amount, 100, "UNSIGNED")

    def sign(self, prepared, broadcaster):
        broadcaster(b"raw")
        return "txid1"


class Spy:
    def __call__(self, raw):
        return "txid1"


def _gate(tmp_path, notifier=None):
    policy = Policy("btc", "signet", "sat", tmp_path / "policy", lambda: (10_000, 10_000, frozenset()))
    audit = AuditLog(tmp_path / "audit.log")
    gate = PaymentGate({"btc": Fake()}, {"btc": policy}, {"btc": Spy()}, lambda: SECRET, auditor=audit, notifier=notifier)
    return gate, audit


def _text(path):
    return path.read_text()


def test_prepare_send_and_bad_code_are_logged_without_secrets(tmp_path):
    gate, audit = _gate(tmp_path)
    audit.record("prepare_ok", coin="btc", approval_code="abcdefabcdef", secret=SECRET, address=ADDR, amount=1, ok=True)
    refused = gate.prepare_payment("btc", "bad", 1000)
    assert not refused["ok"]
    prepared = gate.prepare_payment("btc", ADDR, 1000)
    assert prepared["ok"]
    bad = gate.send_payment("btc", prepared["confirm_id"], "000000000000")
    assert not bad["ok"]
    code = approval_code(SECRET, "btc", "signet", "hot", prepared["confirm_id"], ADDR, 1000, 100)
    assert gate.send_payment("btc", prepared["confirm_id"], code)["ok"]
    text = _text(tmp_path / "audit.log")
    assert "prepare_fail" in text and "prepare_ok" in text and "approve_fail" in text and "send_ok" in text
    assert SECRET not in text
    assert code not in text
    assert "abcdefabcdef" not in text
    assert oct((tmp_path / "audit.log").stat().st_mode & 0o777) == "0o600"


def test_webhook_fires_on_prepare_and_cannot_block_it(tmp_path):
    seen = []

    def poster(url, payload):
        seen.append((url, payload))

    gate, _audit = _gate(tmp_path, WebhookNotifier("https://hooks.example/wallet", poster=poster))
    prepared = gate.prepare_payment("btc", ADDR, 500)
    assert prepared["ok"] and len(seen) == 1
    body = seen[0][1]
    assert body["event"] == "approval_requested"
    assert body["confirm_id"] == prepared["confirm_id"]
    assert "approval_code" not in body and SECRET not in json.dumps(body)

    def explode(url, payload):
        raise RuntimeError("down")

    gate2, _audit2 = _gate(tmp_path / "b", WebhookNotifier("https://hooks.example/wallet", poster=explode))
    again = gate2.prepare_payment("btc", ADDR, 500)
    assert again["ok"]
    assert "webhook_fail" in _text(tmp_path / "b" / "audit.log")


def test_webhook_refuses_non_local_http(tmp_path):
    def poster(*_args):
        raise AssertionError("posted")

    gate, _audit = _gate(tmp_path, WebhookNotifier("http://evil.example/hook", poster=poster))
    prepared = gate.prepare_payment("btc", ADDR, 500)
    assert prepared["ok"]
    assert "webhook url refused" in _text(tmp_path / "audit.log")


def test_cli_approve_does_not_log_the_code(tmp_path, monkeypatch, capsys):
    import argparse
    from wallet_mcp.cli import cmd_approve
    from wallet_mcp.config import load_config

    env = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    cfg = load_config(env)
    policy = Policy("btc", cfg.coins["btc"].network, "sat", cfg.policy_dir("btc"), lambda: (50_000, 100_000, frozenset()))
    pending = policy.add_pending(ADDR, 1000, 10, "memo", "UNSIGNED")
    monkeypatch.setattr("wallet_mcp.cli._tty", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    cmd_approve(cfg, argparse.Namespace(coin="btc", confirm_id=pending.confirm_id))
    out = capsys.readouterr().out
    code = out.split("Approval code:", 1)[1].strip().split()[0]
    text = (tmp_path / "audit.log").read_text()
    assert "approve_ok" in text
    assert code not in text and SECRET not in text


def test_cli_declined_approval_is_logged(tmp_path, monkeypatch):
    import argparse
    from wallet_mcp.cli import cmd_approve
    from wallet_mcp.config import load_config

    env = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    cfg = load_config(env)
    policy = Policy("btc", cfg.coins["btc"].network, "sat", cfg.policy_dir("btc"), lambda: (50_000, 100_000, frozenset()))
    pending = policy.add_pending(ADDR, 1000, 10, "", "UNSIGNED")
    monkeypatch.setattr("wallet_mcp.cli._tty", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    with pytest.raises(SystemExit):
        cmd_approve(cfg, argparse.Namespace(coin="btc", confirm_id=pending.confirm_id))
    text = (tmp_path / "audit.log").read_text()
    assert "approve_fail" in text and "not approved" in text
    assert SECRET not in text
