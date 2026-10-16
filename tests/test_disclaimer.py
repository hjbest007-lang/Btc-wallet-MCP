"""Operator warnings. The approval secret is never part of the text."""
import argparse

from wallet_mcp.cli import cmd_configure
from wallet_mcp.config import apply_enabled, load_config
from wallet_mcp.configure import render_page
from wallet_mcp.disclaimer import DUTCH_SUMMARY, MAINNET_WARNING, SHORT_WARNING

SECRET = "human-only-secret-123"


def env(tmp_path, **extra):
    base = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    base.update(extra)
    return base


def test_configure_prints_english_and_dutch(tmp_path, capsys):
    cfg = load_config(env(tmp_path))
    cmd_configure(cfg, argparse.Namespace(web=False, enable="btc", disable=None, port=8765))
    out = capsys.readouterr().out
    assert "unaudited" in out
    assert "MIT" in out
    assert "AML/KYC" in out
    assert "Nederlandse samenvatting" in out
    assert "niet aansprakelijk" in out
    assert SECRET not in out


def test_configure_page_shows_disclaimer(tmp_path):
    cfg = load_config(env(tmp_path))
    page = render_page(cfg, "token")
    assert SHORT_WARNING in page
    assert DUTCH_SUMMARY in page
    assert "not liable" in page or "not liable" in SHORT_WARNING


def test_enabling_mainnet_warns(tmp_path):
    from wallet_mcp.coins import MAINNET_OPT_IN, allow_mainnet_var

    raw_env = env(tmp_path)
    raw_env[allow_mainnet_var("eth")] = MAINNET_OPT_IN
    cfg = load_config(raw_env)
    cfg.raw["coins"]["eth"]["network"] = "mainnet"
    from wallet_mcp.config import save_config
    save_config(cfg)
    cfg = load_config(raw_env)
    warnings = apply_enabled(cfg, {"eth"})
    assert any(MAINNET_WARNING in item for item in warnings)
    assert SECRET not in " ".join(warnings)
