"""Only ticked coins are constructed and registered as MCP tools."""
import pytest

from wallet_mcp.adapters.base import UnconfiguredCold, UnconfiguredHot
from wallet_mcp.config import apply_enabled, load_config
from wallet_mcp.registry import Bundle, build_runtime, tool_names
from wallet_mcp.server import make_app

SECRET = "human-only-secret-123"


def env(tmp_path):
    return {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }


def test_only_enabled_factories_and_tools(tmp_path):
    cfg = load_config(env(tmp_path))
    apply_enabled(cfg, {"btc", "xrp"})
    cfg = load_config(env(tmp_path))
    called = []

    def factory(coin_id):
        def build(_cfg):
            called.append(coin_id)
            coin = _cfg.coins[coin_id]
            hot = UnconfiguredHot(coin_id, coin.network, coin.spec.unit, coin.spec.experimental)
            cold = UnconfiguredCold(coin_id, coin.network, coin.spec.unit, coin.spec.experimental)

            def explode(_payload):
                raise AssertionError("broadcast")

            return Bundle(hot, cold, explode)
        return build

    factories = {coin: factory(coin) for coin in ("btc", "eth", "avax", "xrp", "ltc")}
    runtime = build_runtime(cfg, factories)
    assert called == ["btc", "xrp"]
    names = tool_names(runtime)
    assert "btc_prepare_payment" in names and "btc_send_payment" in names
    assert "xrp_hot_get_balance" in names and "xrp_cold_get_address" in names
    for blocked in ("eth_prepare_payment", "avax_send_payment", "ltc_get_config",
                    "btc_cold_send_payment", "xrp_cold_prepare_payment", "execute", "broadcast"):
        assert blocked not in names

    app = make_app(runtime)
    registered = {tool.name for tool in app._tool_manager.list_tools()}
    assert registered == set(names)
    btc_prepare = app._tool_manager.get_tool("btc_prepare_payment").fn("tb1q", 1)
    xrp_prepare = app._tool_manager.get_tool("xrp_prepare_payment").fn("rAddr", 1)
    assert "btc" in btc_prepare["error"] and "xrp" in xrp_prepare["error"]
    assert not any("seed" in name or "secret" in name or "sign" == name for name in registered)


def test_disabled_real_factories_are_not_used(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    cfg = load_config(env(tmp_path))
    apply_enabled(cfg, {"btc", "eth"})
    cfg = load_config(env(tmp_path))
    runtime = build_runtime(cfg)  # real factories, no wallets on disk, so no RPC client is built
    assert set(runtime.enabled()) == {"btc", "eth"}
    assert runtime.hot_balance("eth")["ok"] is False
    assert "not initialized" in runtime.hot_address("btc")["error"]
