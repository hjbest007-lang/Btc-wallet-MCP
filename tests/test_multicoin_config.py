"""Per-coin config: test networks by default, mainnet opt-in, limit ceiling."""
import pytest

from wallet_mcp.coins import MAINNET_OPT_IN, allow_mainnet_var
from wallet_mcp.config import ConfigError, apply_enabled, approve_limits, load_config, propose_limits
from wallet_mcp.policy import limit_approval_code

SECRET = "human-only-secret-123"


def env(tmp_path, **extra):
    base = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    base.update(extra)
    return base


def test_defaults_are_test_networks_and_disabled(tmp_path):
    cfg = load_config(env(tmp_path))
    assert cfg.coins["btc"].network == "signet" and not cfg.coins["btc"].enabled
    assert cfg.coins["eth"].network == "sepolia"
    assert cfg.coins["avax"].network == "fuji"
    assert cfg.coins["xrp"].network == "testnet" and cfg.coins["xrp"].spec.experimental
    assert cfg.coins["ltc"].network == "testnet" and cfg.coins["ltc"].spec.experimental
    assert cfg.coins["eth"].spec.status == "tested"
    assert cfg.coins["avax"].chain_id == 43113
    assert not cfg.coins["btc"].is_mainnet


@pytest.mark.parametrize("coin,network", [
    ("btc", "bitcoin"),
    ("btc", "mainnet"),
    ("eth", "mainnet"),
    ("avax", "mainnet"),
    ("xrp", "mainnet"),
    ("ltc", "mainnet"),
])
def test_mainnet_requires_per_coin_opt_in(tmp_path, coin, network):
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"][coin]["network"] = network
    cfg.raw["coins"][coin]["enabled"] = True
    from wallet_mcp.config import save_config
    save_config(cfg)
    with pytest.raises(ConfigError, match="mainnet"):
        load_config(env(tmp_path))
    opted = env(tmp_path)
    opted[allow_mainnet_var(coin)] = MAINNET_OPT_IN
    loaded = load_config(opted)
    assert loaded.coins[coin].is_mainnet and loaded.coins[coin].enabled


def test_unknown_network_rejected(tmp_path):
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"]["eth"]["network"] = "holesky"
    from wallet_mcp.config import save_config
    save_config(cfg)
    with pytest.raises(ConfigError, match="unsupported"):
        load_config(env(tmp_path))


def test_enable_does_not_touch_limits(tmp_path):
    cfg = load_config(env(tmp_path))
    apply_enabled(cfg, {"btc", "xrp"})
    again = load_config(env(tmp_path))
    assert again.enabled_ids() == ["btc", "xrp"]
    assert again.coins["btc"].max_per_tx == 50_000
    assert again.coins["xrp"].spec.experimental


def test_cannot_enable_mainnet_without_flag(tmp_path):
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"]["eth"]["network"] = "mainnet"
    from wallet_mcp.config import save_config
    save_config(cfg)
    cfg = load_config(env(tmp_path))
    with pytest.raises(ConfigError, match="ALLOW_MAINNET"):
        apply_enabled(cfg, {"eth"})
    assert not load_config(env(tmp_path)).coins["eth"].enabled


def test_lower_limit_applies_raise_needs_code(tmp_path):
    cfg = load_config(env(tmp_path))
    status, request = propose_limits(cfg, "btc", 1000, 2000)
    assert status == "applied" and request is None
    assert load_config(env(tmp_path)).coins["btc"].max_per_tx == 1000

    cfg = load_config(env(tmp_path))
    status, request = propose_limits(cfg, "btc", 80_000, 100_000)
    assert status == "pending"
    assert load_config(env(tmp_path)).coins["btc"].max_per_tx == 1000

    cfg = load_config(env(tmp_path))
    with pytest.raises(ConfigError):
        approve_limits(cfg, request.request_id, "000000000000", SECRET)
    assert load_config(env(tmp_path)).coins["btc"].max_per_tx == 1000

    code = limit_approval_code(SECRET, request.request_id, "btc", 80_000, 100_000)
    approve_limits(load_config(env(tmp_path)), request.request_id, code, SECRET)
    raised = load_config(env(tmp_path)).coins["btc"]
    assert raised.max_per_tx == 80_000 and raised.approved_max_per_tx == 80_000


def test_tampered_raise_is_rejected(tmp_path):
    cfg = load_config(env(tmp_path))
    cfg.raw["coins"]["eth"]["max_per_tx"] = 10**18
    cfg.raw["coins"]["eth"]["approved_max_per_tx"] = 10**18
    cfg.raw["coins"]["eth"]["limits_mac"] = "deadbeef"
    from wallet_mcp.config import save_config
    save_config(cfg)
    with pytest.raises(ConfigError, match="not approved"):
        load_config(env(tmp_path))
