"""Checkbox CLI and the localhost page. The page cannot raise limits."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from wallet_mcp.cli import cmd_init, main
from wallet_mcp.config import load_config
from wallet_mcp.configure import interactive_select, make_httpd

SECRET = "human-only-secret-123"


def env_of(tmp_path):
    return {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }


def test_interactive_toggle_and_quit(tmp_path):
    answers = iter(["1", "2", "1", "q"])
    seen = []
    result = interactive_select(set(), lambda _prompt: next(answers), seen.append)
    assert result is None
    assert "[x] Bitcoin" in seen[1]
    assert "[ ] Bitcoin" in seen[-1]


def test_interactive_save(tmp_path):
    answers = iter(["1", "4", "s"])
    result = interactive_select(set(), lambda _prompt: next(answers), lambda _line: None)
    assert result == {"btc", "xrp"}


def test_cli_enable_writes_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLET_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WALLET_MCP_CONFIG", str(tmp_path / "config.json"))
    main(["configure", "--enable", "btc,eth", "--disable", "eth"])
    cfg = load_config(env_of(tmp_path))
    assert cfg.enabled_ids() == ["btc"]


def test_cold_init_refused_on_agent_host(tmp_path):
    import argparse
    cfg = load_config({**env_of(tmp_path), "WALLET_MCP_MODE": "agent"})
    with __import__("pytest").raises(SystemExit, match="cold"):
        cmd_init(cfg, argparse.Namespace(coin="btc", role="cold"))


def test_web_ui_selects_coins_and_cannot_raise_limits(tmp_path):
    base = env_of(tmp_path)
    load_config(base)  # defaults
    httpd = make_httpd(base, 0)
    assert httpd.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        token = page.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        assert "Coins selecteren" in page
        assert 'name="coin_btc"' in page and 'name="coin_xrp"' in page

        refused = urllib.request.Request(
            f"http://127.0.0.1:{port}/", data=b"coin_btc=on", method="POST"
        )
        try:
            urllib.request.urlopen(refused)
            assert False, "csrf-less post should fail"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert not load_config(base).coins["btc"].enabled

        body = urllib.parse.urlencode({
            "csrf": token,
            "coin_btc": "on",
            "coin_avax": "on",
            "tx_btc": "90000",  # above the 50000 default
            "day_btc": "100000",
            "tx_eth": "1000",
            "day_eth": "2000",
        }).encode()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(urllib.request.Request(f"http://127.0.0.1:{port}/", data=body, method="POST"))
        except urllib.error.HTTPError as exc:
            assert exc.code == 303
            location = exc.headers["Location"]
            assert "approve-limit" in urllib.parse.unquote(location)
        cfg = load_config(base)
        assert cfg.coins["btc"].enabled and cfg.coins["avax"].enabled
        assert not cfg.coins["eth"].enabled
        assert cfg.coins["btc"].max_per_tx == 50_000  # increase not applied
        assert cfg.coins["eth"].max_per_tx == 1000  # decrease applied even while eth is off
        pending = json.loads((tmp_path / "limit-requests.json").read_text())
        assert len(pending) == 1
        request = next(iter(pending.values()))
        assert request["coin"] == "btc" and request["max_per_tx"] == 90000
    finally:
        httpd.shutdown()
