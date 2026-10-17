"""One-click launcher: env filter, agent refusal, port choice, already running.

Windows registry and console calls are injected. CI runs this on Linux.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wallet_mcp.cli import main
from wallet_mcp.coins import COIN_ORDER, MAINNET_OPT_IN, allow_mainnet_var
from wallet_mcp.config import load_config, save_config
from wallet_mcp.launch import (
    AGENT_REFUSAL,
    CONSOLE_TITLE,
    DEFAULT_PORT,
    HOST,
    STOP_HINT,
    _hkcu_environment_values,
    allow_mainnet_from_items,
    apply_allow_mainnet,
    choose_port,
    dashboard_url,
    fetch_root,
    is_allow_mainnet_name,
    port_is_free,
    read_allow_mainnet_flags,
    run,
    wait_until_answers,
)
from wallet_mcp.launch import main as beheer_main

ROOT = Path(__file__).resolve().parents[1]
SECRET = "human-only-secret-123"
DECOY = "registry-only-secret"


def _entry():
    path = ROOT / "scripts" / "launcher_entry.py"
    spec = importlib.util.spec_from_file_location("launcher_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY = _entry()


def _env(tmp_path, **extra):
    base = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    base.update(extra)
    return base


class _Quiet(BaseHTTPRequestHandler):
    body = b"not the dashboard"

    def do_GET(self):
        data = type(self).body
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def _background(body: bytes):
    handler = type("Handler", (_Quiet,), {"body": body})
    httpd = ThreadingHTTPServer((HOST, 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, name="probe", daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if fetch_root(httpd.server_address[1], 0.2) is not None:
            return httpd
        time.sleep(0.01)
    _stop(httpd)
    raise RuntimeError("probe server did not start")


def _stop(httpd) -> None:
    httpd.shutdown()
    httpd.server_close()


def test_pattern_matches_every_coin_and_rejects_other_names():
    for coin_id in COIN_ORDER:
        assert is_allow_mainnet_name(allow_mainnet_var(coin_id))
    rejected = [
        "WALLET_MCP_APPROVAL_SECRET",
        "WALLET_MCP_MODE",
        "WALLET_MCP_PASSPHRASE",
        "WALLET_MCP_DATA_DIR",
        "WALLET_MCP_CONFIG",
        "WALLET_MCP_APPROVAL_WEBHOOK_URL",
        "WALLET_MCP_USDT_AVAX_TOKEN",
        "WALLET_MCP_ALLOW_MAINNET",
        "WALLET_MCP_BTC_ALLOW_MAINNET_EXTRA",
        "WALLET_MCP_BTC_ALLOW_MAIN",
        "XWALLET_MCP_BTC_ALLOW_MAINNET",
        "wallet_mcp_btc_allow_mainnet",
        "WALLET_MCP__ALLOW_MAINNET",
        "WALLET_MCP_BTC_ALLOW_MAINNET ",
        "WALLET_MCP_BTC_ALLOW_MAINNET\n",
        "",
        "PATH",
        "BTC_WALLET_ALLOW_MAINNET",
    ]
    for name in rejected:
        assert not is_allow_mainnet_name(name), name


def test_apply_copies_only_allow_mainnet_names():
    env = {
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
        "WALLET_MCP_MODE": "owner",
        "PATH": "/usr/bin",
        "WALLET_MCP_BTC_ALLOW_MAINNET": "stale",
        "WALLET_MCP_LTC_ALLOW_MAINNET": MAINNET_OPT_IN,
    }
    registry = {
        "WALLET_MCP_BTC_ALLOW_MAINNET": MAINNET_OPT_IN,
        "WALLET_MCP_USDC_ETH_ALLOW_MAINNET": MAINNET_OPT_IN,
        "WALLET_MCP_USDT_AVAX_ALLOW_MAINNET": MAINNET_OPT_IN,
        "WALLET_MCP_APPROVAL_SECRET": DECOY,
        "WALLET_MCP_MODE": "agent",
        "WALLET_MCP_PASSPHRASE": "do-not-copy",
        "WALLET_MCP_DATA_DIR": "C:\\somewhere",
        "WALLET_MCP_USDT_AVAX_TOKEN": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
        "WALLET_MCP_BTC_ALLOW_MAINNET_EXTRA": MAINNET_OPT_IN,
        "NOT_WALLET_MCP_ETH_ALLOW_MAINNET": MAINNET_OPT_IN,
        "PATH": "C:\\Windows",
        "WALLET_MCP_ETH_ALLOW_MAINNET": 1,
    }
    apply_allow_mainnet(env, registry)
    assert env["WALLET_MCP_BTC_ALLOW_MAINNET"] == MAINNET_OPT_IN
    assert env["WALLET_MCP_USDC_ETH_ALLOW_MAINNET"] == MAINNET_OPT_IN
    assert env["WALLET_MCP_USDT_AVAX_ALLOW_MAINNET"] == MAINNET_OPT_IN
    assert env["WALLET_MCP_LTC_ALLOW_MAINNET"] == MAINNET_OPT_IN
    assert env["WALLET_MCP_APPROVAL_SECRET"] == SECRET
    assert env["WALLET_MCP_MODE"] == "owner"
    assert env["PATH"] == "/usr/bin"
    assert "WALLET_MCP_PASSPHRASE" not in env
    assert "WALLET_MCP_DATA_DIR" not in env
    assert "WALLET_MCP_USDT_AVAX_TOKEN" not in env
    assert "WALLET_MCP_ETH_ALLOW_MAINNET" not in env
    assert DECOY not in env.values()


def test_apply_does_not_expand_percent_values():
    env = {"WALLET_MCP_APPROVAL_SECRET": SECRET}
    apply_allow_mainnet(env, {"WALLET_MCP_BTC_ALLOW_MAINNET": "%WALLET_MCP_APPROVAL_SECRET%"})
    assert env["WALLET_MCP_BTC_ALLOW_MAINNET"] == "%WALLET_MCP_APPROVAL_SECRET%"
    assert env["WALLET_MCP_APPROVAL_SECRET"] == SECRET


def test_registry_items_drop_non_matching_names():
    selected = allow_mainnet_from_items([
        ("WALLET_MCP_SOL_ALLOW_MAINNET", MAINNET_OPT_IN, 1),
        ("WALLET_MCP_APPROVAL_SECRET", DECOY, 1),
        ("WALLET_MCP_MODE", "agent", 1),
        ("PATH", "C:\\Windows", 2),
        ("WALLET_MCP_ETH_ALLOW_MAINNET", None, 1),
    ])
    assert selected == {"WALLET_MCP_SOL_ALLOW_MAINNET": MAINNET_OPT_IN}


def test_user_environment_reader_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def boom():
        raise AssertionError("registry was opened")

    monkeypatch.setattr("wallet_mcp.launch._hkcu_environment_values", boom)
    assert read_allow_mainnet_flags() == {}


def test_windows_reader_filters_hkcu_values(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    rows = [
        ("WALLET_MCP_BTC_ALLOW_MAINNET", MAINNET_OPT_IN, 1),
        ("WALLET_MCP_APPROVAL_SECRET", DECOY, 1),
        ("WALLET_MCP_MODE", "agent", 1),
        ("WALLET_MCP_USDC_ETH_ALLOW_MAINNET", MAINNET_OPT_IN, 1),
    ]
    monkeypatch.setattr("wallet_mcp.launch._hkcu_environment_values", lambda: rows)
    assert read_allow_mainnet_flags() == {
        "WALLET_MCP_BTC_ALLOW_MAINNET": MAINNET_OPT_IN,
        "WALLET_MCP_USDC_ETH_ALLOW_MAINNET": MAINNET_OPT_IN,
    }


def test_hkcu_enumeration_keeps_only_allow_mainnet_and_closes():
    class Key:
        def __init__(self):
            self.rows = [
                ("WALLET_MCP_BTC_ALLOW_MAINNET", MAINNET_OPT_IN, 1),
                ("WALLET_MCP_APPROVAL_SECRET", DECOY, 1),
                ("WALLET_MCP_ETH_ALLOW_MAINNET", 4, 4),
                ("PATH", "C:\\Windows", 1),
                ("WALLET_MCP_XRP_ALLOW_MAINNET", MAINNET_OPT_IN, 1),
            ]
            self.closed = False

        def EnumValue(self, index):
            if index >= len(self.rows):
                raise OSError(22, "end")
            return self.rows[index]

        def Close(self):
            self.closed = True

    key = Key()
    found = _hkcu_environment_values(open_key=lambda: key)
    assert found == [
        ("WALLET_MCP_BTC_ALLOW_MAINNET", MAINNET_OPT_IN),
        ("WALLET_MCP_XRP_ALLOW_MAINNET", MAINNET_OPT_IN),
    ]
    assert key.closed

    def missing():
        raise OSError("missing")

    assert _hkcu_environment_values(open_key=missing) == ()


@pytest.mark.parametrize("mode", ["agent", "Agent", "AGENT", " agent "])
def test_launch_refuses_agent_mode_without_probing_or_opening(mode):
    def probe(_port):
        raise AssertionError("probed")

    def factory(_env, _port):
        raise AssertionError("bound")

    opened = []
    env = {"WALLET_MCP_MODE": mode, "WALLET_MCP_APPROVAL_SECRET": SECRET}
    registry = {"WALLET_MCP_MODE": "owner", "WALLET_MCP_BTC_ALLOW_MAINNET": MAINNET_OPT_IN}
    with pytest.raises(SystemExit, match="WALLET_MCP_MODE=agent") as caught:
        run(env=env, registry=registry, opener=opened.append, probe=probe, server_factory=factory)
    assert str(caught.value) == AGENT_REFUSAL
    assert opened == []
    assert env["WALLET_MCP_MODE"] == mode
    assert env["WALLET_MCP_APPROVAL_SECRET"] == SECRET
    assert env["WALLET_MCP_BTC_ALLOW_MAINNET"] == MAINNET_OPT_IN


def test_cli_launch_refuses_agent_before_reading_config(tmp_path, monkeypatch):
    broken = tmp_path / "config.json"
    broken.write_text("{", encoding="utf-8")
    monkeypatch.setenv("WALLET_MCP_CONFIG", str(broken))
    monkeypatch.setenv("WALLET_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WALLET_MCP_MODE", "agent")
    with pytest.raises(SystemExit, match="agent") as caught:
        main(["launch"])
    assert "cannot parse" not in str(caught.value)


def test_cli_launch_passes_the_port(monkeypatch):
    seen = {}

    def fake_run(port=DEFAULT_PORT, **_kwargs):
        seen["port"] = port
        return dashboard_url(port)

    monkeypatch.setattr("wallet_mcp.launch.run", fake_run)
    main(["launch", "--port", "8799"])
    assert seen == {"port": 8799}
    main(["launch"])
    assert seen == {"port": 8770}


def test_choose_port_skips_busy_ports_and_gives_up():
    busy = {8770, 8771}

    def is_free(_host, port):
        return port not in busy

    assert choose_port(8770, is_free=is_free) == 8772
    with pytest.raises(SystemExit, match="no free port from 8770 through 8772"):
        choose_port(8770, is_free=lambda _host, _port: False, attempts=3)
    with pytest.raises(SystemExit, match="port must be"):
        choose_port(0)
    with pytest.raises(SystemExit, match="port must be"):
        choose_port(True)


def test_choose_port_skips_a_listening_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    sock.listen(1)
    busy = sock.getsockname()[1]
    try:
        chosen = choose_port(busy, attempts=30)
    finally:
        sock.close()
    assert chosen != busy
    assert chosen > busy


def test_port_is_free_refuses_a_non_loopback_host():
    assert port_is_free("10.1.2.3", 9) is False
    assert port_is_free("localhost", 9) is False


def test_already_running_opens_the_browser_and_does_not_bind(capsys):
    opened = []

    def probe(port):
        assert port == 8770
        return True

    def factory(_env, _port):
        raise AssertionError("bound")

    url = run(
        env={"WALLET_MCP_MODE": "owner", "WALLET_MCP_APPROVAL_SECRET": SECRET},
        registry={},
        opener=opened.append,
        probe=probe,
        server_factory=factory,
    )
    assert url == "http://127.0.0.1:8770/"
    assert opened == [url]
    out = capsys.readouterr().out
    assert "Wallet-beheer draait al: http://127.0.0.1:8770/" in out
    assert STOP_HINT not in out
    assert SECRET not in out


def test_default_opener_is_webbrowser(monkeypatch):
    opened = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    run(env={"WALLET_MCP_MODE": "owner"}, registry={}, probe=lambda _port: True)
    assert opened == ["http://127.0.0.1:8770/"]


def test_real_probe_detects_a_dashboard_and_skips_a_foreign_listener(tmp_path, capsys):
    marker = _background(b"<html><head><title>Wallet-beheer</title></head><body></body></html>")
    foreign = _background(b"not the dashboard")
    try:
        opened = []

        def factory(_env, _port):
            raise AssertionError("bound")

        port = marker.server_address[1]
        url = run(
            port=port,
            env=_env(tmp_path),
            registry={},
            opener=opened.append,
            server_factory=factory,
        )
        assert url == dashboard_url(port)
        assert opened == [url]

        started = {}

        def hold(httpd):
            started["port"] = httpd.server_address[1]
            status, body = fetch_root(started["port"])
            assert status == 200
            assert b"<title>Wallet-beheer</title>" in body
            assert SECRET.encode() not in body

        opened.clear()
        url = run(
            port=foreign.server_address[1],
            env=_env(tmp_path),
            registry={"WALLET_MCP_APPROVAL_SECRET": DECOY, "WALLET_MCP_MODE": "agent"},
            opener=opened.append,
            hold=hold,
        )
        assert started["port"] != foreign.server_address[1]
        assert url == dashboard_url(started["port"])
        assert opened == [url]
        assert HOST in url and "localhost" not in url
    finally:
        _stop(marker)
        _stop(foreign)
    out = capsys.readouterr().out
    assert CONSOLE_TITLE in out
    assert STOP_HINT in out
    assert SECRET not in out
    assert DECOY not in out


def test_refresh_is_visible_to_the_dashboard_and_writes_no_seed(tmp_path, capsys):
    env = _env(tmp_path)
    cfg = load_config(env)
    cfg.raw["coins"]["btc"]["network"] = "bitcoin"
    cfg.raw["coins"]["btc"]["enabled"] = True
    save_config(cfg)
    seen = {}

    def hold(httpd):
        seen["port"] = httpd.server_address[1]
        status, body = fetch_root(seen["port"])
        text = body.decode("utf-8", "replace")
        assert status == 200
        assert "MAINNET" in text
        assert SECRET not in text
        assert DECOY not in text
        assert MAINNET_OPT_IN not in text

    url = run(
        port=choose_port(21000),
        env=env,
        registry={
            "WALLET_MCP_BTC_ALLOW_MAINNET": MAINNET_OPT_IN,
            "WALLET_MCP_APPROVAL_SECRET": DECOY,
            "WALLET_MCP_MODE": "agent",
            "WALLET_MCP_USDT_AVAX_TOKEN": "0xabc",
        },
        opener=lambda _url: None,
        hold=hold,
    )
    assert url == dashboard_url(seen["port"])
    assert env["WALLET_MCP_BTC_ALLOW_MAINNET"] == MAINNET_OPT_IN
    assert env["WALLET_MCP_APPROVAL_SECRET"] == SECRET
    assert env.get("WALLET_MCP_MODE") != "agent"
    assert "WALLET_MCP_USDT_AVAX_TOKEN" not in env
    assert fetch_root(seen["port"], 0.3) is None
    names = [path.name for path in tmp_path.rglob("*")]
    assert not any(name.endswith(".enc") or "seed" in name for name in names)
    out = capsys.readouterr().out
    assert STOP_HINT in out
    assert SECRET not in out
    assert DECOY not in out


def test_keyboard_interrupt_closes_the_port(tmp_path):
    seen = {}

    def hold(httpd):
        seen["port"] = httpd.server_address[1]
        raise KeyboardInterrupt

    run(
        port=choose_port(22000),
        env=_env(tmp_path),
        registry={},
        opener=lambda _url: None,
        hold=hold,
    )
    assert fetch_root(seen["port"], 0.3) is None


def test_wait_retries_then_times_out():
    calls = {"n": 0}

    def fetch(_port, _timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return 200, b"ok"

    wait_until_answers(1, timeout=5, fetch=fetch, pause=lambda _seconds: None)
    assert calls["n"] == 3
    with pytest.raises(SystemExit, match="did not answer on 127.0.0.1:1"):
        wait_until_answers(1, timeout=0, fetch=lambda _port, _timeout: None, pause=lambda _seconds: None)


def test_launcher_does_not_create_seeds_and_scripts_omit_the_secret():
    launch_text = (ROOT / "src" / "wallet_mcp" / "launch.py").read_text(encoding="utf-8")
    for banned in (
        "generate_mnemonic",
        "write_seed_file",
        "write_master",
        "new_secret",
        "cmd_init",
        "cmd_backup",
        "read_seed_file",
    ):
        assert banned not in launch_text
    entry_text = (ROOT / "scripts" / "launcher_entry.py").read_text(encoding="utf-8")
    assert "\nimport wallet_mcp" not in entry_text
    assert "\nfrom wallet_mcp" not in entry_text
    assert "shell=True" not in entry_text
    for banned in ("bdkpython", "cryptography", "solders", "eth_account", "xrpl", "embit", "APPROVAL_SECRET"):
        assert banned not in entry_text
    for name in ("build_launcher.ps1", "install_shortcut.ps1", "Wallet-beheer.cmd"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "APPROVAL_SECRET" not in script
        assert "Get-ChildItem Env" not in script
        for line in script.splitlines():
            assert line.strip().lower() != "set"


def test_windows_scripts_are_crlf_and_build_a_thin_exe():
    build = (ROOT / "scripts" / "build_launcher.ps1").read_bytes()
    shortcut = (ROOT / "scripts" / "install_shortcut.ps1").read_bytes()
    cmd = (ROOT / "scripts" / "Wallet-beheer.cmd").read_bytes()
    for raw in (build, shortcut, cmd):
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")
    text = build.decode("ascii")
    assert "PyInstaller" in text
    assert "--onefile" in text
    assert "--console" in text
    assert "--noconsole" not in text
    assert "--windowed" not in text
    assert "--exclude-module" in text
    assert "wallet_mcp" in text
    assert "launcher_entry.py" in text
    assert "Wallet-beheer.cmd" in text
    assert "python.exe -m wallet_mcp launch" in text
    shortcut_text = shortcut.decode("ascii")
    assert "WScript.Shell" in shortcut_text
    assert "IconLocation" in shortcut_text
    assert "Wallet-beheer.exe" in shortcut_text
    assert "Wallet-beheer.cmd" in shortcut_text
    assert "Desktop" in shortcut_text
    assert "wallet-beheer.ico" in shortcut_text
    cmd_text = cmd.decode("ascii")
    assert "-m wallet_mcp launch" in cmd_text
    assert "WALLET_BEHEER_PYTHON" in cmd_text
    assert "Btc-wallet-MCP" in cmd_text
    assert "title Wallet-beheer" in cmd_text


def test_pyproject_registers_the_console_script_and_pyinstaller():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["wallet-beheer"] == "wallet_mcp.launch:main"
    assert scripts["wallet-mcp"] == "wallet_mcp.cli:main"
    assert "pyinstaller>=6" in data["project"]["optional-dependencies"]["dev"]
    assert "gui-scripts" not in data["project"]


def test_readme_documents_the_one_click_launcher():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Wallet-beheer starten met één klik" in text
    assert "Sluit dit venster om te stoppen" in text
    assert "dist\\Wallet-beheer.exe" in text or "dist\\Wallet-beheer.exe" in text.replace("\\\\", "\\")
    assert "install_shortcut.ps1" in text
    assert "WALLET_MCP_<COIN>_ALLOW_MAINNET" in text


def test_icon_is_a_real_ico():
    data = (ROOT / "assets" / "wallet-beheer.ico").read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"
    count = int.from_bytes(data[4:6], "little")
    assert count >= 2


def test_resolve_python_configured_relative_and_home(tmp_path):
    home = tmp_path / "home"
    configured = tmp_path / "custom" / "python.exe"
    configured.parent.mkdir()
    configured.write_bytes(b"")
    repo = tmp_path / "repo"
    venv = repo / ".venv" / "Scripts" / "python.exe"
    venv.parent.mkdir(parents=True)
    venv.write_bytes(b"")
    exe = repo / "dist" / "Wallet-beheer.exe"
    fallback = home / "Btc-wallet-MCP" / ".venv" / "Scripts" / "python.exe"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"")

    found = ENTRY.resolve_python(
        exe, home=home, environ={"WALLET_BEHEER_PYTHON": str(configured)}, exists=Path.exists,
    )
    assert found == configured
    found = ENTRY.resolve_python(exe, home=home, environ={}, exists=Path.exists)
    assert found == venv
    desktop = tmp_path / "Desktop" / "Wallet-beheer.exe"
    found = ENTRY.resolve_python(desktop, home=home, environ={}, exists=Path.exists)
    assert found == fallback
    with pytest.raises(SystemExit, match="WALLET_BEHEER_PYTHON"):
        ENTRY.resolve_python(
            exe,
            home=home,
            environ={"WALLET_BEHEER_PYTHON": str(tmp_path / "missing.exe")},
            exists=Path.exists,
        )
    with pytest.raises(SystemExit, match="niet gevonden"):
        ENTRY.resolve_python(exe, home=tmp_path / "empty", environ={}, exists=lambda _path: False)


def test_entry_spawns_the_venv_module_not_itself(tmp_path):
    home = tmp_path / "home"
    python = home / "Btc-wallet-MCP" / ".venv" / "Scripts" / "python.exe"
    seen = {}

    class Result:
        returncode = 0

    def runner(command, check):
        seen["command"] = command
        seen["check"] = check
        return Result()

    code = ENTRY.main(
        ["--port", "8788"],
        runner=runner,
        environ={},
        home=home,
        exe=tmp_path / "Desktop" / "Wallet-beheer.exe",
        exists=lambda path: path == python,
    )
    assert code == 0
    assert seen["check"] is False
    assert seen["command"] == [str(python), "-m", "wallet_mcp", "launch", "--port", "8788"]
    assert Path(seen["command"][0]).name == "python.exe"

    class Failed:
        returncode = 3

    assert ENTRY.main(
        [],
        runner=lambda command, check: Failed(),
        environ={},
        home=home,
        exe=tmp_path / "Desktop" / "Wallet-beheer.exe",
        exists=lambda path: path == python,
    ) == 3


def test_help_lists_launch_and_wallet_beheer_has_no_seed_commands(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "launch" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        beheer_main(["--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--port" in help_text
    assert "init" not in help_text
    assert "backup" not in help_text


def test_module_launch_refuses_agent_without_printing_the_secret(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "WALLET_MCP_MODE": "agent",
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    result = subprocess.run(
        [sys.executable, "-m", "wallet_mcp", "launch"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "agent" in result.stderr
    assert SECRET not in result.stderr
    assert SECRET not in result.stdout
