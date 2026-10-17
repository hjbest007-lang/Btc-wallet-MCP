"""Owner dashboard: loopback only, and the approval secret never leaves the POST body."""
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPConnection

import pytest

from wallet_mcp.adapters.base import ChainAdapter, PreparedTransfer
from wallet_mcp.audit import AuditLog
from wallet_mcp.cli import cmd_dashboard, main
from wallet_mcp.config import apply_enabled, load_config, save_config
from wallet_mcp.dashboard import load_eur_prices, make_dashboard
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, PolicyError, approval_code

SECRET = "human-only-secret-123"
ADDR = "tb1qfm5lzy0ptv8nwt7hxqy7jrtz0swpa3x7tkl0ad"


def env_of(tmp_path):
    return {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }


def enable_btc(env):
    cfg = load_config(env)
    apply_enabled(cfg, {"btc"})
    return load_config(env)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Fake(ChainAdapter):
    unit = "sat"
    experimental = False

    def __init__(self):
        self.coin_id = "btc"
        self.network = "signet"
        self.role = "hot"
        self.can_sign = True
        self.signed = 0

    def balance(self):
        return {"total": 100_000_000, "unit": "sat"}

    def sign(self, prepared, broadcaster):
        self.signed += 1
        broadcaster(b"raw")
        return "txid-dashboard"


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "txid-dashboard"


class _View:
    def __init__(self, gate):
        self.gate = gate

    def hot_balance(self, coin):
        return {"ok": True, "coin": coin, "total": 100_000_000, "unit": "sat"}

    def cold_balance(self, coin):
        return {"ok": True, "coin": coin, "total": 2_000, "unit": "sat"}


def _serve(env, **kwargs):
    httpd = make_dashboard(env, 0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _stop(httpd):
    httpd.shutdown()
    httpd.server_close()


def _get(httpd, path="/", headers=None):
    port = httpd.server_address[1]
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read().decode(), dict(response.headers)


def _post(httpd, fields, headers=None):
    port = httpd.server_address[1]
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=data, method="POST", headers=headers or {},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=5)
        return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


def _raw(httpd, method, body=b"", headers=None):
    port = httpd.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    merged = {"Content-Length": str(len(body))}
    merged.update(headers or {})
    conn.request(method, "/", body=body, headers=merged)
    response = conn.getresponse()
    payload = response.read().decode()
    status, hdrs = response.status, dict(response.getheaders())
    conn.close()
    return status, payload, hdrs


def _token(page):
    return page.split('name="csrf" value="', 1)[1].split('"', 1)[0]


def _limit_fields(token, **overrides):
    fields = {
        "csrf": token,
        "action": "limits",
        "coin": "btc",
        "max_per_tx": "50000",
        "max_per_day": "100000",
        "auto_max_per_tx": "0",
        "auto_max_per_day": "0",
    }
    fields.update(overrides)
    return fields


def test_dashboard_binds_localhost_only(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    httpd = _serve(env)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        assert httpd.socket.getsockname()[0] == "127.0.0.1"
        status, page, headers = _get(httpd)
        assert status == 200
        assert "Wallet-beheer" in page
        assert "SIGNET" in page
        assert "badge mainnet" not in page
        assert "Vandaag zelf uitgegeven" in page
        assert "alleen bekijken" in page
        assert "Wacht op jouw goedkeuring" in page
        assert "Logboek" in page
        assert "experimentele, niet-geauditeerde software" in page
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cache-Control"] == "no-store"
        assert "script-src 'unsafe-inline'" in headers["Content-Security-Policy"]
        assert "default-src 'none'" in headers["Content-Security-Policy"]
    finally:
        _stop(httpd)


def test_dashboard_rejects_bad_host_and_origin(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    httpd = _serve(env)
    try:
        _status, page, _headers = _get(httpd)
        token = _token(page)
        fields = _limit_fields(token, max_per_tx="90000", approval_secret=SECRET)
        body = urllib.parse.urlencode(fields).encode()
        port = httpd.server_address[1]
        status, payload, _hdrs = _raw(httpd, "POST", body, {"Host": "evil.example", "Content-Type": "application/x-www-form-urlencoded"})
        assert status == 403 and payload == "bad host"
        assert SECRET not in payload
        status, payload, _hdrs = _raw(
            httpd, "POST", body,
            {"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == 403 and payload == "bad origin"
        status, payload, _hdrs = _raw(httpd, "GET", headers={"Host": "evil.example"})
        assert status == 403 and token not in payload
        assert load_config(env).coins["btc"].max_per_tx == 50_000
    finally:
        _stop(httpd)


def test_dashboard_rejects_missing_csrf(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    httpd = _serve(env)
    try:
        status, payload, _headers = _post(httpd, _limit_fields("nope", max_per_tx="90000", approval_secret=SECRET))
        assert status == 403 and "csrf" in payload
        assert SECRET not in payload
        assert load_config(env).coins["btc"].max_per_tx == 50_000
    finally:
        _stop(httpd)


def test_dashboard_never_echoes_approval_secret(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    root = logging.getLogger()
    root.addHandler(handler)
    httpd = _serve(env)
    try:
        _status, page, _headers = _get(httpd)
        token = _token(page)
        status, _body, headers = _post(
            httpd, _limit_fields(token, max_per_tx="90000", max_per_day="150000", approval_secret=SECRET),
        )
        assert status == 303
        location = urllib.parse.unquote(headers["Location"])
        assert SECRET not in location
        followed, shown, _hdrs = _get(httpd, headers["Location"])
        assert followed == 200
        assert SECRET not in shown
        assert "value=\"" + SECRET not in shown
        blob = (tmp_path / "config.json").read_text()
        assert SECRET not in blob
        audit = (tmp_path / "audit.log").read_text() if (tmp_path / "audit.log").exists() else ""
        assert SECRET not in audit
        assert SECRET not in " ".join(records)
        assert load_config(env).coins["btc"].max_per_tx == 90_000
    finally:
        root.removeHandler(handler)
        _stop(httpd)


def test_dashboard_rejects_raise_without_secret(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    httpd = _serve(env)
    try:
        _status, page, _headers = _get(httpd)
        token = _token(page)
        status, _body, headers = _post(httpd, _limit_fields(token, max_per_tx="10000", max_per_day="20000"))
        assert status == 303
        assert "verlaagd" in urllib.parse.unquote(headers["Location"])
        assert load_config(env).coins["btc"].max_per_tx == 10_000

        status, _body, headers = _post(httpd, _limit_fields(
            token, max_per_tx="90000", max_per_day="20000", auto_max_per_tx="1000", auto_max_per_day="2000",
        ))
        assert status == 303
        notice = urllib.parse.unquote(headers["Location"])
        assert "geweigerd" in notice
        assert SECRET not in notice
        cfg = load_config(env)
        assert cfg.coins["btc"].max_per_tx == 10_000
        assert cfg.coins["btc"].auto_max_per_tx == 0
        assert not (tmp_path / "limit-requests.json").exists()
        assert not (tmp_path / "auto-requests.json").exists()

        status, _body, headers = _post(httpd, _limit_fields(
            token, max_per_tx="90000", max_per_day="20000", approval_secret="not-the-real-secret",
        ))
        assert status == 303
        assert "invalid" in urllib.parse.unquote(headers["Location"])
        assert load_config(env).coins["btc"].max_per_tx == 10_000
    finally:
        _stop(httpd)


def test_dashboard_approve_flow(tmp_path):
    env = env_of(tmp_path)
    cfg = enable_btc(env)
    coin = cfg.coins["btc"]
    policy = Policy(
        "btc", coin.network, coin.spec.unit, cfg.policy_dir("btc"),
        lambda c=coin: (c.max_per_tx, c.max_per_day, c.allowlist),
        secret_provider=lambda: SECRET,
        auto_limits=lambda c=coin: (c.auto_max_per_tx, c.auto_max_per_day),
    )
    spy = _Spy()
    adapter = _Fake()
    gate = PaymentGate(
        {"btc": adapter}, {"btc": policy}, {"btc": spy}, lambda: SECRET,
        auditor=AuditLog(cfg.data_dir / "audit.log"),
    )
    pending = policy.add_pending(ADDR, 1000, 150, "koffie", "UNSIGNED")
    code = approval_code(SECRET, "btc", "signet", "hot", pending.confirm_id, ADDR, 1000, 150)

    httpd = _serve(env, runtime_factory=lambda _cfg: _View(gate))
    try:
        _status, page, _headers = _get(httpd)
        assert "Wacht op jouw goedkeuring" in page
        assert ADDR in page
        assert "koffie" in page
        assert "Goedkeuren" in page and "Afwijzen" in page
        token = _token(page)
        status, body, headers = _post(httpd, {
            "csrf": token,
            "action": "approve",
            "coin": "btc",
            "confirm_id": pending.confirm_id,
            "approval_secret": SECRET,
        })
        assert status == 303
        location = urllib.parse.unquote(headers["Location"])
        assert "goedgekeurd" in location
        assert SECRET not in location and code not in location
        assert SECRET not in body and code not in body
        assert spy.calls == [b"raw"] and adapter.signed == 1
        with pytest.raises(PolicyError):
            policy.get_pending(pending.confirm_id)
        audit = (tmp_path / "audit.log").read_text()
        assert SECRET not in audit and code not in audit
        assert '"auto": false' in audit or '"auto":false' in audit or "send_ok" in audit
        _status, shown, _hdrs = _get(httpd, headers["Location"])
        assert SECRET not in shown and code not in shown
        assert "door jou goedgekeurd" in shown
    finally:
        _stop(httpd)


def test_dashboard_refuses_agent_mode(tmp_path, monkeypatch):
    import argparse

    base = env_of(tmp_path)
    monkeypatch.setenv("WALLET_MCP_DATA_DIR", base["WALLET_MCP_DATA_DIR"])
    monkeypatch.setenv("WALLET_MCP_CONFIG", base["WALLET_MCP_CONFIG"])
    monkeypatch.setenv("WALLET_MCP_APPROVAL_SECRET", SECRET)
    monkeypatch.setenv("WALLET_MCP_MODE", "agent")
    with pytest.raises(SystemExit, match="agent"):
        main(["dashboard", "--port", "0"])
    cfg = load_config({**base, "WALLET_MCP_MODE": "agent"})
    with pytest.raises(SystemExit, match="agent"):
        cmd_dashboard(cfg, argparse.Namespace(port=0))
    with pytest.raises(SystemExit, match="does not start"):
        make_dashboard({**base, "WALLET_MCP_MODE": "agent"}, 0)


def test_eur_estimate_is_optional_and_https_only(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    called = []

    def boom(url):
        called.append(url)
        raise OSError("down")

    httpd = _serve(env, runtime_factory=lambda _cfg: _View(None), price_fetcher=boom)
    try:
        _status, quiet, _headers = _get(httpd)
        assert called == []
        assert "\u20ac" not in quiet
        _status, failed, _headers = _get(httpd, "/?eur=1")
        assert called and called[0].startswith("https://")
        assert "\u2013" in failed
    finally:
        _stop(httpd)

    def prices(url):
        assert url.startswith("https://")
        return {"bitcoin": {"eur": 10}}

    httpd = _serve(env, runtime_factory=lambda _cfg: _View(None), price_fetcher=prices)
    try:
        _status, page, _headers = _get(httpd, "/?eur=1")
        assert "\u20ac 10,00" in page
    finally:
        _stop(httpd)

    seen = []

    def refuse(url):
        seen.append(url)
        return {"bitcoin": {"eur": 1}}

    assert load_eur_prices(refuse, "http://127.0.0.1/price") == {}
    assert seen == []
    assert load_eur_prices(refuse, "https://example.com/price")["btc"] == 1.0


def test_mainnet_badge_is_amber(tmp_path):
    from wallet_mcp.coins import MAINNET_OPT_IN, allow_mainnet_var

    env = env_of(tmp_path)
    env[allow_mainnet_var("btc")] = MAINNET_OPT_IN
    cfg = load_config(env)
    cfg.raw["coins"]["btc"]["network"] = "bitcoin"
    cfg.raw["coins"]["btc"]["enabled"] = True
    save_config(cfg)
    httpd = _serve(env)
    try:
        _status, page, _headers = _get(httpd)
        assert 'class="badge mainnet"' in page
        assert ">MAINNET<" in page
    finally:
        _stop(httpd)


def test_reject_drops_pending_payment(tmp_path):
    env = env_of(tmp_path)
    cfg = enable_btc(env)
    coin = cfg.coins["btc"]
    policy = Policy(
        "btc", coin.network, coin.spec.unit, cfg.policy_dir("btc"),
        lambda c=coin: (c.max_per_tx, c.max_per_day, c.allowlist),
        secret_provider=lambda: SECRET,
    )
    pending = policy.add_pending(ADDR, 500, 100, "nee", "UNSIGNED")
    httpd = _serve(env, runtime_factory=lambda _cfg: _View(None))
    try:
        _status, page, _headers = _get(httpd)
        status, _body, headers = _post(httpd, {
            "csrf": _token(page),
            "action": "reject",
            "coin": "btc",
            "confirm_id": pending.confirm_id,
        })
        assert status == 303
        assert "afgewezen" in urllib.parse.unquote(headers["Location"])
        with pytest.raises(PolicyError):
            policy.get_pending(pending.confirm_id)
        assert SECRET not in (tmp_path / "audit.log").read_text()
    finally:
        _stop(httpd)


def test_limit_raise_with_secret_seals_mac(tmp_path):
    env = env_of(tmp_path)
    enable_btc(env)
    httpd = _serve(env)
    try:
        _status, page, _headers = _get(httpd)
        status, _body, headers = _post(httpd, _limit_fields(
            _token(page),
            max_per_tx="90000",
            max_per_day="150000",
            auto_max_per_tx="1000",
            auto_max_per_day="2000",
            approval_secret=SECRET,
        ))
        assert status == 303
        assert "verhoogd" in urllib.parse.unquote(headers["Location"])
        cfg = load_config(env)
        assert cfg.coins["btc"].max_per_tx == 90_000
        assert cfg.coins["btc"].max_per_day == 150_000
        assert cfg.coins["btc"].auto_max_per_tx == 1_000
        assert cfg.coins["btc"].auto_max_per_day == 2_000
        assert cfg.coins["btc"].limits_mac
        assert cfg.coins["btc"].auto_mac
        assert SECRET not in json.dumps(cfg.raw)
    finally:
        _stop(httpd)
