"""Cold hardware signing. The agent cannot start it, and nothing is broadcast."""
import pytest

from wallet_mcp.adapters.base import ChainAdapter
from wallet_mcp.auth import SpendAuthorization
from wallet_mcp.coins import COINS
from wallet_mcp.config import load_config
from wallet_mcp.hardware import (
    HardwareError,
    LedgerDevice,
    ScriptedDevice,
    TrezorDevice,
    build_eip712_document,
    build_psbt_document,
    device_by_name,
    payload_hash,
    sign_cold_payment,
)
from wallet_mcp.policy import Policy, approval_code, hardware_approval_code
from wallet_mcp.registry import tool_names
from wallet_mcp.server import make_app

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)


def _sign(document, device, mode="owner", tty_ok=True, approval=None, secret=SECRET, broadcaster=None):
    summary_address = document["outputs"][0]["address"] if document["format"] == "psbt-v0" else document["address"]
    amount = document["outputs"][0]["value"] if document["format"] == "psbt-v0" else document["amount"]
    fee = document["fee"]
    code = approval if approval is not None else hardware_approval_code(
        secret, document["coin"], document["network"], summary_address, amount, fee,
        payload_hash(document),
    )
    return sign_cold_payment(
        mode=mode, document=document, approval=code, secret=secret, device=device,
        tty_ok=tty_ok, broadcaster=broadcaster,
    )


def test_agent_cannot_start_signing_even_if_the_device_would_confirm():
    document = build_psbt_document("btc", "signet", ADDR, 5_000_000, 500)
    device = ScriptedDevice(confirm=True)
    spy = Spy()
    with pytest.raises(HardwareError, match="agent cannot"):
        _sign(document, device, mode="agent", broadcaster=spy)
    assert device.seen == [] and spy.calls == []


def test_missing_physical_confirmation_does_not_return_a_signature():
    document = build_psbt_document("ltc", "testnet", "tltc1q6rz28mcfaxtmd6v789l9rrlrusdprr9pesrjxk", 20_000, 100)
    device = ScriptedDevice(confirm=False)
    with pytest.raises(HardwareError, match="physical confirmation"):
        _sign(document, device)
    assert device.seen and device.seen[0][0] == "psbt"


def test_confirmed_psbt_and_eip712_are_not_broadcast():
    psbt = build_psbt_document("btc", "signet", ADDR, 9_000_000, 400)
    device = ScriptedDevice(confirm=True)
    signed = _sign(psbt, device)
    assert signed["broadcast"] is False and signed["signature_hex"]
    assert device.seen[0][1].startswith(b"psbt\xff")

    typed = build_eip712_document(
        "eth", "sepolia", COINS["eth"].networks["sepolia"].chain_id,
        "0x0000000000000000000000000000000000000001", 10**15, 21_000,
    )
    evm = ScriptedDevice(confirm=True)
    signed_evm = _sign(typed, evm)
    assert signed_evm["broadcast"] is False
    assert evm.seen[0][0] == "eip712"
    assert evm.seen[0][1]["message"]["to"].endswith("0001")


def test_hot_approval_code_does_not_sign_and_broadcast_is_refused():
    document = build_psbt_document("btc", "signet", ADDR, 1000, 100)
    hot = approval_code(SECRET, "btc", "signet", "hot", "pay_x", ADDR, 1000, 100)
    device = ScriptedDevice(confirm=True)
    with pytest.raises(HardwareError, match="invalid approval code"):
        _sign(document, device, approval=hot)
    assert device.seen == []
    spy = Spy()
    with pytest.raises(HardwareError, match="does not broadcast"):
        _sign(document, device, broadcaster=spy)
    assert spy.calls == [] and device.seen == []


def test_ledger_and_trezor_do_not_sign_without_a_device():
    with pytest.raises(HardwareError, match="not sign a cold key"):
        LedgerDevice().sign_psbt(b"psbt\xff", {})
    with pytest.raises(HardwareError, match="not sign a cold key"):
        TrezorDevice().sign_eip712({}, {})
    with pytest.raises(HardwareError, match="physical confirmation"):
        LedgerDevice(transport=ScriptedDevice(confirm=False)).sign_psbt(b"psbt\xff\x00", {"address": ADDR})


def test_non_tty_and_unknown_device_are_refused():
    document = build_psbt_document("btc", "signet", ADDR, 1000, 50)
    with pytest.raises(HardwareError, match="interactive terminal"):
        _sign(document, ScriptedDevice(True), tty_ok=False)
    with pytest.raises(HardwareError, match="ledger or trezor"):
        device_by_name("software")


def test_hw_sign_is_not_an_mcp_tool(tmp_path):
    from wallet_mcp.adapters.base import UnconfiguredCold, UnconfiguredHot
    from wallet_mcp.config import apply_enabled
    from wallet_mcp.registry import Bundle, build_runtime

    cfg = load_config({
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    })
    apply_enabled(cfg, {"btc", "eth"})
    cfg = load_config({
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    })

    def factory(coin_id):
        def build(app_cfg):
            coin = app_cfg.coins[coin_id]
            hot = UnconfiguredHot(coin_id, coin.network, coin.spec.unit, coin.spec.experimental)
            cold = UnconfiguredCold(coin_id, coin.network, coin.spec.unit, coin.spec.experimental)
            return Bundle(hot, cold, lambda _payload: (_ for _ in ()).throw(AssertionError("broadcast")))
        return build

    runtime = build_runtime(cfg, {coin: factory(coin) for coin in ("btc", "eth")})
    names = tool_names(runtime)
    assert not any("hw" in name or "ledger" in name or "trezor" in name for name in names)
    registered = {tool.name for tool in make_app(runtime)._tool_manager.list_tools()}
    assert "hw_sign" not in registered and "btc_send_payment" in registered


def test_cli_refuses_agent_mode(tmp_path):
    import argparse
    from wallet_mcp.cli import cmd_hw_sign

    cfg = load_config({
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_MODE": "agent",
    })
    with pytest.raises(SystemExit, match="agent cannot"):
        cmd_hw_sign(cfg, argparse.Namespace(device="ledger", file="missing.json"))


def test_hardware_path_does_not_touch_the_hot_gate(tmp_path):
    """A large cold signature does not spend, and does not raise, the hot limit."""
    document = build_psbt_document("btc", "signet", ADDR, 50_000_000, 1000)
    device = ScriptedDevice(confirm=True)
    signed = _sign(document, device)
    assert signed["amount"] == 50_000_000
    policy = Policy("btc", "signet", "sat", tmp_path, lambda: (50_000, 100_000, frozenset()))
    assert policy.spent_last_24h() == 0
    with pytest.raises(Exception):
        SpendAuthorization(
            coin="btc", network="signet", role="cold", confirm_id="pay_hw",
            address=ADDR, amount=50_000_000, fee=1000, seal=object(),
        )
    assert "hw_sign" not in {name for name in dir(ChainAdapter)}
