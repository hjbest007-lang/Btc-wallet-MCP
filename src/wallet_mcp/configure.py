"""Checkbox configuration: interactive CLI and a localhost-only web page.

Both write the same config file. Enabling a coin loads its adapter on the next
`serve`. Limits can be lowered here. Raising a limit only creates a pending
request; the page has no way to supply the approval secret.
"""
from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .coins import COIN_ORDER, COINS
from .config import ConfigError, apply_enabled, load_config, propose_limits
from .disclaimer import DUTCH_SUMMARY, SHORT_WARNING

MAX_BODY = 64_000


def render_menu(enabled: set[str]) -> str:
    lines = ["Select coins. Enter a number to toggle, s to save, q to quit."]
    for index, coin_id in enumerate(COIN_ORDER, start=1):
        spec = COINS[coin_id]
        mark = "x" if coin_id in enabled else " "
        flag = "experimental" if spec.experimental else "tested"
        lines.append(
            f"  {index}. [{mark}] {spec.name} ({spec.symbol}) — {spec.default_network} — {flag}"
        )
    return "\n".join(lines)


def interactive_select(enabled: set[str], input_fn, output_fn) -> set[str] | None:
    """Returns the new selection, or None if the operator quits without saving."""
    current = set(enabled)
    output_fn(render_menu(current))
    while True:
        line = input_fn("toggle number, s=save, q=quit: ").strip().lower()
        if line == "s":
            return current
        if line == "q":
            return None
        if line.isdigit() and 1 <= int(line) <= len(COIN_ORDER):
            coin_id = COIN_ORDER[int(line) - 1]
            if coin_id in current:
                current.remove(coin_id)
            else:
                current.add(coin_id)
            output_fn(render_menu(current))
        else:
            output_fn("enter a coin number, s, or q")


def render_page(cfg, token: str, notice: str = "") -> str:
    rows = []
    for coin_id in COIN_ORDER:
        coin = cfg.coins[coin_id]
        spec = coin.spec
        checked = " checked" if coin.enabled else ""
        status = "experimental" if spec.experimental else "tested"
        rows.append(f"""
        <fieldset>
          <legend>{html.escape(spec.name)} ({html.escape(spec.symbol)})</legend>
          <label><input type="checkbox" name="coin_{coin_id}" value="on"{checked}> enabled</label>
          <p>Network <strong>{html.escape(coin.network)}</strong> · {status} · unit {html.escape(spec.unit)}</p>
          <p>Current limits: {coin.max_per_tx} per tx, {coin.max_per_day} per day
             (approved ceiling {coin.approved_max_per_tx} / {coin.approved_max_per_day}).</p>
          <label>Per tx <input name="tx_{coin_id}" inputmode="numeric" value="{coin.max_per_tx}"></label>
          <label>Per day <input name="day_{coin_id}" inputmode="numeric" value="{coin.max_per_day}"></label>
        </fieldset>""")
    note = f"<p class='notice'>{html.escape(notice)}</p>" if notice else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>wallet-mcp coins</title>
<style>
  body {{ font: 16px/1.4 sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; }}
  fieldset {{ margin: 1rem 0; }}
  .notice {{ background: #f4f1e8; padding: .75rem; }}
  .warn {{ background: #f8e8e4; padding: .75rem; }}
  button {{ font: inherit; padding: .4rem .8rem; }}
</style></head><body>
<h1>Coins selecteren</h1>
<p class="warn">{html.escape(SHORT_WARNING)}</p>
<p class="warn">{html.escape(DUTCH_SUMMARY)}</p>
<p>Only checked coins are loaded as MCP tools the next time the server starts.
The cold wallet stays on your machine. This page cannot raise a limit: an increase
is stored as a request until you run <code>wallet-mcp approve-limit</code>.</p>
{note}
<form method="post" action="/">
  <input type="hidden" name="csrf" value="{html.escape(token)}">
  {''.join(rows)}
  <button type="submit">Save</button>
</form>
</body></html>"""


def apply_form(cfg, fields: dict) -> str:
    enabled = {coin_id for coin_id in COIN_ORDER if _field(fields, f"coin_{coin_id}") == "on"}
    warnings = apply_enabled(cfg, enabled)
    notes = list(warnings)
    # Reload so limit checks see the saved selection and the current ceilings.
    cfg = load_config(cfg.env)
    for coin_id in COIN_ORDER:
        raw_tx = _field(fields, f"tx_{coin_id}")
        raw_day = _field(fields, f"day_{coin_id}")
        if raw_tx == "" and raw_day == "":
            continue
        try:
            status, request = propose_limits(cfg, coin_id, int(raw_tx), int(raw_day))
        except (ConfigError, ValueError) as exc:
            notes.append(f"{coin_id}: {exc}")
            continue
        if status == "pending" and request is not None:
            notes.append(
                f"{coin_id}: limit increase is waiting for approval. "
                f"Run `wallet-mcp approve-limit {request.request_id}` in your terminal."
            )
        elif status == "applied":
            notes.append(f"{coin_id}: limits lowered")
    return " ".join(notes)


def _field(fields: dict, name: str) -> str:
    values = fields.get(name) or []
    return values[0].strip() if values else ""


def make_httpd(env=None, port: int = 8765) -> ThreadingHTTPServer:
    import secrets
    token = secrets.token_hex(16)
    env_snapshot = dict(os.environ if env is None else env)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            notice = ""
            if "?" in self.path:
                notice = parse_qs(self.path.split("?", 1)[1]).get("notice", [""])[0]
            try:
                cfg = load_config(env_snapshot)
            except ConfigError as exc:
                self._send(400, str(exc))
                return
            self._send(200, render_page(cfg, token, notice), "text/html; charset=utf-8")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                self._send(413, "request too large")
                return
            bound_port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{bound_port}", f"localhost:{bound_port}"}
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host and host not in allowed:
                self._send(403, "bad host")
                return
            if origin and origin not in {f"http://{item}" for item in allowed}:
                self._send(403, "bad origin")
                return
            raw = self.rfile.read(length).decode()
            fields = parse_qs(raw, keep_blank_values=True)
            given = _field(fields, "csrf")
            import hmac
            if len(given) != len(token) or not hmac.compare_digest(given, token):
                self._send(403, "bad csrf token")
                return
            try:
                cfg = load_config(env_snapshot)
                notice = apply_form(cfg, fields)
            except ConfigError as exc:
                notice = str(exc)
            self.send_response(303)
            self.send_header("Location", "/?notice=" + _url(notice))
            self.end_headers()

        def log_message(self, fmt, *args):
            return

        def _send(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8"):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

    # Localhost only. There is no host argument that can widen this.
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _url(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")
