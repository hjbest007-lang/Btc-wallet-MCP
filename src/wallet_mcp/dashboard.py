"""Localhost owner dashboard. Not an MCP tool, and not for agent mode.

The page follows the configure web UI: it binds 127.0.0.1 only, checks Host
and Origin, sends the same no-store / nosniff headers, and requires the CSRF
token minted for this process. Lowering a limit or an autonomous budget
applies immediately through ``propose_limits`` / ``propose_auto_budget``.
Raising either one calls those same functions and then ``approve_limits`` /
``approve_auto_budget`` with an HMAC from ``policy``. Approving a payment
computes ``approval_code`` and hands it to ``PaymentGate.send_payment``.

The approval secret is read from the POST body, used for that call, and
dropped. It is not written to disk, the audit log, the query string, or the
HTML response. Init and backup stay on the TTY; this page never creates or
shows a seed.
"""
from __future__ import annotations

import html
import json
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote

from btc_wallet_mcp.guard import GuardError, sanitize_terminal, validate_chain_url

from .adapters.base import AdapterError
from .audit import AuditLog
from .coins import COINS
from .config import (
    ConfigError,
    approve_auto_budget,
    approve_limits,
    load_config,
    propose_auto_budget,
    propose_limits,
)
from .configure import client_refused, csrf_matches
from .disclaimer import DUTCH_SUMMARY, SHORT_WARNING
from .policy import Policy, PolicyError, approval_code, auto_approval_code, limit_approval_code
from .util import format_amount

MAX_BODY = 64_000
_CONFIRM = re.compile(r"^pay_[0-9a-f]{16}$")
_DASH = "\u2013"

# Display-only. The gate, the ledger, and the autonomous budget never read this.
_EUR_IDS = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "avax": "avalanche-2",
    "xrp": "ripple",
    "ltc": "litecoin",
    "usdc_eth": "usd-coin",
    "usdt_eth": "tether",
    "usdc_avax": "usd-coin",
    "usdt_avax": "tether",
    "sol": "solana",
}
_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price?vs_currencies=eur&ids="
    + ",".join(sorted(set(_EUR_IDS.values())))
)
_SCALE = {
    "sat": 100_000_000,
    "litoshi": 100_000_000,
    "wei": 10**18,
    "drop": 1_000_000,
    "token": 1_000_000,
    "lamport": 1_000_000_000,
}
_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def _field(fields: dict, name: str) -> str:
    values = fields.get(name) or []
    return values[0].strip() if values else ""


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        text = text.replace(secret, "")
    return text[:400]


def _hostname() -> str:
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    cleaned = sanitize_terminal(name or "").strip()
    return (cleaned or "localhost")[:80]


def _policies_for(cfg) -> dict[str, Policy]:
    def secret() -> str | None:
        return cfg.env.get("WALLET_MCP_APPROVAL_SECRET")

    found = {}
    for coin_id in cfg.enabled_ids():
        coin = cfg.coins[coin_id]
        found[coin_id] = Policy(
            coin_id,
            coin.network,
            coin.spec.unit,
            cfg.policy_dir(coin_id),
            lambda c=coin: (c.max_per_tx, c.max_per_day, c.allowlist),
            ttl=coin.spec.confirm_ttl_seconds,
            secret_provider=secret,
            auto_limits=lambda c=coin: (c.auto_max_per_tx, c.auto_max_per_day),
        )
    return found


def _base_amount(body: dict) -> int | None:
    if not isinstance(body, dict):
        return None
    for key in ("total", "total_sats", "confirmed"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def load_eur_prices(fetcher, url: str = _PRICE_URL) -> dict[str, float]:
    """Map coin id to a EUR price. Any failure, including a non-https URL, yields {}."""
    try:
        clean = validate_chain_url(url, mainnet=True)
    except GuardError:
        return {}
    if not clean.lower().startswith("https://"):
        return {}
    try:
        payload = fetcher(clean)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    found: dict[str, float] = {}
    for coin_id, gecko_id in _EUR_IDS.items():
        row = payload.get(gecko_id)
        if not isinstance(row, dict):
            continue
        raw = row.get("eur")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            continue
        found[coin_id] = float(raw)
    return found


def _fetch_price_document(url: str) -> dict:
    from btc_wallet_mcp.guard import open_chain

    with open_chain(
        url,
        headers={"Accept": "application/json", "User-Agent": "wallet-mcp"},
        timeout=4,
        mainnet=True,
    ) as response:
        raw = response.read(1_000_000)
    payload = json.loads(raw.decode())
    if not isinstance(payload, dict):
        raise ValueError("price payload")
    return payload


def format_eur(amount: int, unit: str, price: float) -> str | None:
    scale = _SCALE.get(unit)
    if scale is None or price < 0 or amount < 0:
        return None
    eur = (amount / scale) * price
    text = f"{eur:,.2f}"
    text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
    return "\u20ac " + text


def _network_badge(cfg) -> tuple[str, str]:
    enabled = [cfg.coins[coin_id] for coin_id in cfg.enabled_ids()]
    if any(coin.is_mainnet for coin in enabled):
        return "MAINNET", "badge mainnet"
    labels: list[str] = []
    for coin in enabled:
        label = coin.network.upper()
        if label not in labels:
            labels.append(label)
    if not labels:
        return "GEEN NETWERK", "badge"
    return " \u00b7 ".join(labels), "badge"


def _slider_max(current: int, default: int) -> int:
    return min(max(current, default, 1) * 4, 10**15)


def _audit_rows(path, limit: int = 40) -> list[dict]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines()[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        safe = {}
        for key in ("event", "ts", "coin", "address", "amount", "fee", "ok", "auto", "error", "txid"):
            if key in item:
                safe[key] = item[key]
        rows.append(safe)
    rows.reverse()
    return rows


def _audit_label(row: dict) -> str:
    if row.get("auto") is True:
        return "autonoom"
    if row.get("event") in {"send_ok", "approve_ok"}:
        return "door jou goedgekeurd"
    if row.get("event") == "approve_fail":
        return "afgewezen"
    return ""


def _when(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _default_runtime(cfg):
    from .registry import build_runtime

    return build_runtime(cfg)


def _load_runtime(cfg, factory):
    try:
        return factory(cfg), ""
    except (ConfigError, AdapterError, PolicyError) as exc:
        return None, str(exc)
    except Exception:
        return None, "wallet niet geladen"


def _balance_of(runtime, coin_id: str, role: str) -> dict:
    if runtime is None:
        return {"ok": False, "error": "niet beschikbaar"}
    method = runtime.hot_balance if role == "hot" else runtime.cold_balance
    try:
        body = method(coin_id)
    except (AdapterError, ConfigError, PolicyError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "niet beschikbaar"}
    return body if isinstance(body, dict) else {"ok": False, "error": "niet beschikbaar"}


def _spent(policy: Policy) -> int | None:
    try:
        return policy.auto_spent_last_24h()
    except PolicyError:
        return None


def _pending(policy: Policy) -> list:
    try:
        return policy.pending_payments()
    except PolicyError:
        return []


def render_dashboard(cfg, token: str, runtime, prices: dict[str, float] | None, notice: str, show_eur: bool) -> str:
    badge, badge_class = _network_badge(cfg)
    policies = {}
    try:
        policies = _policies_for(cfg)
    except (ConfigError, PolicyError, OSError):
        policies = {}
    hot_cards = []
    cold_rows = []
    limit_forms = []
    waiting = []
    for coin_id in cfg.enabled_ids():
        coin = cfg.coins[coin_id]
        spec = coin.spec
        policy = policies.get(coin_id)
        hot = _balance_of(runtime, coin_id, "hot")
        amount = _base_amount(hot)
        if amount is None:
            saldo = "niet beschikbaar"
            eur_line = ""
        else:
            saldo = html.escape(format_amount(coin_id, amount))
            eur_line = ""
            if show_eur:
                estimate = None
                if prices is not None and coin_id in prices:
                    estimate = format_eur(amount, spec.unit, prices[coin_id])
                eur_line = f"<p class='eur'>\u2248 {html.escape(estimate or _DASH)}</p>"
        spent = _spent(policy) if policy is not None else None
        cap = coin.auto_max_per_day
        spent_text = _DASH if spent is None else str(spent)
        pct = 0 if spent is None or cap <= 0 else min(100, int(spent * 100 / cap))
        net = "MAINNET" if coin.is_mainnet else coin.network
        hot_cards.append(f"""
        <section class="card">
          <div class="row">
            <h2>{html.escape(spec.name)} <span class="muted">({html.escape(spec.symbol)})</span></h2>
            <span class="muted">{html.escape(net)}</span>
          </div>
          <p class="saldo">Saldo <strong>{saldo}</strong></p>
          {eur_line}
          <p>Vandaag zelf uitgegeven {html.escape(spent_text)} / {cap}</p>
          <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="{cap}" aria-valuenow="{0 if spent is None else spent}">
            <span style="width:{pct}%"></span>
          </div>
        </section>""")
        cold = _balance_of(runtime, coin_id, "cold")
        cold_amount = _base_amount(cold)
        if cold_amount is None:
            cold_text = "niet beschikbaar"
        else:
            cold_text = format_amount(coin_id, cold_amount)
        cold_rows.append(
            f"<li><strong>{html.escape(spec.name)}</strong> "
            f"<span class='muted'>{html.escape(cold_text)}</span></li>"
        )
        limit_forms.append(_limit_form(coin_id, coin, token, show_eur))
        if policy is not None:
            for pending in _pending(policy):
                waiting.append(_pending_block(pending, token))
    if not hot_cards:
        hot_cards.append("<section class='card'><p>Geen coins aangezet. Gebruik <code>wallet-mcp configure</code>.</p></section>")
    cold_body = "".join(cold_rows) or "<li>Geen coins aangezet.</li>"
    limits_body = "".join(limit_forms) or "<p>Geen coins aangezet.</p>"
    waiting_body = "".join(waiting) or "<p class='muted'>Geen betalingen die op je wachten.</p>"
    log_body = _log_html(cfg.data_dir / "audit.log")
    note = f"<p class='notice'>{html.escape(notice)}</p>" if notice else ""
    eur_href = "/?eur=0" if show_eur else "/?eur=1"
    eur_label = "Verberg euroschatting" if show_eur else "Toon schatting in euro"
    return f"""<!DOCTYPE html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet-beheer</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: #121417; color: #e7ecf1; font: 16px/1.45 "Segoe UI", sans-serif; }}
  header {{ display: flex; justify-content: space-between; align-items: center; gap: 1rem;
            padding: 1.1rem 1.4rem; border-bottom: 1px solid #2a313a; }}
  h1 {{ font-size: 1.25rem; margin: 0; font-weight: 650; }}
  h2 {{ font-size: 1.05rem; margin: 0 0 .4rem; }}
  main {{ max-width: 52rem; margin: 0 auto; padding: 1.1rem 1.2rem 3rem; }}
  .badge {{ border-radius: 999px; padding: .2rem .7rem; font-size: .75rem; letter-spacing: .06em;
            background: #243040; color: #c5d0dc; }}
  .badge.mainnet {{ background: #3a2e12; color: #f0b429; }}
  .card {{ background: #1b2128; border: 1px solid #2c3540; border-radius: 12px; padding: 1rem 1.1rem; margin: 0 0 1rem; }}
  .warn, .notice {{ background: #2a221c; color: #f0d7c8; padding: .75rem .9rem; border-radius: 10px; }}
  .notice {{ background: #1e2a24; color: #d5eadc; }}
  .row {{ display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: baseline; }}
  .muted {{ color: #9aa6b2; }}
  .saldo {{ font-size: 1.15rem; margin: .3rem 0; }}
  .eur {{ color: #f0b429; margin-top: 0; }}
  .bar {{ height: 8px; background: #2a313a; border-radius: 99px; overflow: hidden; }}
  .bar > span {{ display: block; height: 100%; background: #e0a84a; }}
  label {{ display: block; margin: .55rem 0 .2rem; }}
  input[type="number"], input[type="password"] {{
    font: inherit; width: 100%; box-sizing: border-box; background: #0f1318; color: inherit;
    border: 1px solid #3a4654; border-radius: 8px; padding: .4rem .55rem; }}
  input[type="range"] {{ width: 100%; accent-color: #e0a84a; }}
  button {{ font: inherit; background: #243044; color: #e7ecf1; border: 1px solid #3d4e66;
            border-radius: 8px; padding: .45rem .85rem; margin: .3rem .4rem .3rem 0; }}
  button.danger {{ background: #3a2424; border-color: #6a3a3a; }}
  a {{ color: #8eb6e0; }}
  ul {{ margin: .4rem 0; padding-left: 1.1rem; }}
  code {{ color: #d5deea; }}
  .actions {{ display: flex; flex-wrap: wrap; align-items: flex-end; gap: .5rem; }}
</style></head><body>
<header>
  <h1>Wallet-beheer \u00b7 {html.escape(_hostname())}</h1>
  <span class="{badge_class}">{html.escape(badge)}</span>
</header>
<main>
<p class="warn">{html.escape(SHORT_WARNING)}</p>
<p class="warn">{html.escape(DUTCH_SUMMARY)}</p>
<p class="muted">Init en backup blijven in de terminal. Deze pagina maakt geen seed aan en toont geen seed.
Het goedkeuringsgeheim wordt niet opgeslagen.</p>
<p><a href="{eur_href}">{eur_label}</a></p>
{note}
{''.join(hot_cards)}
<section class="card">
  <h2>Cold wallet \u00b7 alleen bekijken</h2>
  <ul>{cold_body}</ul>
</section>
<section class="card">
  <h2>Limieten</h2>
  <p class="muted">Verlagen gaat meteen in. Verhogen vraagt het goedkeuringsgeheim en gebruikt dezelfde
  goedkeuring als <code>approve-limit</code> en <code>approve-auto</code>. Bedragen blijven in de basiseenheid.</p>
  {limits_body}
</section>
<section class="card">
  <h2>Wacht op jouw goedkeuring</h2>
  {waiting_body}
</section>
<section class="card">
  <h2>Logboek</h2>
  {log_body}
</section>
</main>
<script>
(function () {{
  var ranges = document.querySelectorAll("input[type=range][data-sync]");
  for (var i = 0; i < ranges.length; i++) {{
    (function (range) {{
      var number = document.getElementById(range.getAttribute("data-sync"));
      if (!number) return;
      range.addEventListener("input", function () {{ number.value = range.value; }});
      number.addEventListener("input", function () {{
        if (!number.value) return;
        var max = Number(range.max);
        var value = Number(number.value);
        range.value = value > max ? String(max) : number.value;
      }});
    }})(ranges[i]);
  }}
}})();
</script>
</body></html>"""


def _pair(field: str, ident: str, label: str, value: int, minimum: int, maximum: int) -> str:
    safe_id = html.escape(ident)
    safe_name = html.escape(field)
    return f"""
    <label for="{safe_id}">{html.escape(label)}</label>
    <input type="range" min="{minimum}" max="{maximum}" value="{value}" data-sync="{safe_id}">
    <input id="{safe_id}" name="{safe_name}" type="number" inputmode="numeric" min="{minimum}" value="{value}">"""


def _limit_form(coin_id: str, coin, token: str, show_eur: bool) -> str:
    spec = coin.spec
    eur = '<input type="hidden" name="eur" value="1">' if show_eur else ""
    unit = spec.unit
    fields = [
        _pair("max_per_tx", coin_id + "_tx", f"Max per transactie ({unit})", coin.max_per_tx, 1,
              _slider_max(coin.max_per_tx, spec.default_max_per_tx)),
        _pair("max_per_day", coin_id + "_day", f"Max per dag ({unit})", coin.max_per_day, 1,
              _slider_max(coin.max_per_day, spec.default_max_per_day)),
        _pair("auto_max_per_tx", coin_id + "_auto_tx", f"Autonoom per transactie ({unit})",
              coin.auto_max_per_tx, 0, _slider_max(coin.auto_max_per_tx, coin.max_per_tx)),
        _pair("auto_max_per_day", coin_id + "_auto_day", f"Autonoom per dag ({unit})",
              coin.auto_max_per_day, 0, _slider_max(coin.auto_max_per_day, coin.max_per_day)),
    ]
    secret_id = html.escape(coin_id) + "_secret"
    return f"""
    <form method="post" action="/" autocomplete="off">
      <input type="hidden" name="csrf" value="{html.escape(token)}">
      <input type="hidden" name="action" value="limits">
      <input type="hidden" name="coin" value="{html.escape(coin_id)}">
      {eur}
      <h3>{html.escape(spec.name)}</h3>
      {''.join(fields)}
      <label for="{secret_id}">Goedkeuringsgeheim</label>
      <input id="{secret_id}" name="approval_secret" type="password" autocomplete="off" spellcheck="false">
      <button type="submit">Toepassen</button>
    </form>"""


def _pending_block(pending, token: str) -> str:
    spec = COINS.get(pending.coin)
    name = spec.name if spec else pending.coin
    extra = ""
    if pending.extra.startswith("tag:"):
        extra = f"<p>Tag {html.escape(sanitize_terminal(pending.extra.split(':', 1)[1]))}</p>"
    elif pending.extra.startswith("gas:"):
        extra = f"<p>Gas {html.escape(sanitize_terminal(pending.extra.split(':', 1)[1]))}</p>"
    memo = sanitize_terminal(pending.memo) or "geen memo"
    hidden = (
        f'<input type="hidden" name="csrf" value="{html.escape(token)}">'
        f'<input type="hidden" name="coin" value="{html.escape(pending.coin)}">'
        f'<input type="hidden" name="confirm_id" value="{html.escape(pending.confirm_id)}">'
    )
    return f"""
    <article>
      <p><strong>{html.escape(name)}</strong> \u00b7 {html.escape(format_amount(pending.coin, pending.amount))}
         \u00b7 fee {html.escape(format_amount(pending.coin, pending.fee))}</p>
      <p class="muted">{html.escape(sanitize_terminal(pending.address))}</p>
      <p>{html.escape(memo)}</p>
      {extra}
      <form method="post" action="/" autocomplete="off" class="actions">
        {hidden}
        <input type="hidden" name="action" value="approve">
        <label>Goedkeuringsgeheim
          <input name="approval_secret" type="password" autocomplete="off" spellcheck="false">
        </label>
        <button type="submit">Goedkeuren</button>
      </form>
      <form method="post" action="/">
        {hidden}
        <input type="hidden" name="action" value="reject">
        <button type="submit" class="danger">Afwijzen</button>
      </form>
    </article>"""


def _log_html(path) -> str:
    rows = _audit_rows(path)
    if not rows:
        return "<p class='muted'>Logboek is leeg.</p>"
    items = []
    for row in rows:
        label = _audit_label(row)
        label_html = f" <strong>{html.escape(label)}</strong>" if label else ""
        address = sanitize_terminal(str(row.get("address") or ""))
        amount = row.get("amount")
        amount_html = html.escape(str(amount)) if isinstance(amount, int) else ""
        items.append(
            "<li>"
            f"{html.escape(_when(row.get('ts')))} "
            f"{html.escape(str(row.get('coin') or ''))} "
            f"{html.escape(str(row.get('event') or ''))}"
            f"{label_html} {amount_html} {html.escape(address)}"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _integers(fields: dict) -> tuple[int, int, int, int]:
    try:
        return (
            int(_field(fields, "max_per_tx"), 10),
            int(_field(fields, "max_per_day"), 10),
            int(_field(fields, "auto_max_per_tx"), 10),
            int(_field(fields, "auto_max_per_day"), 10),
        )
    except ValueError:
        raise ConfigError("limiet moet een geheel getal zijn") from None


def apply_limit_form(env: dict, fields: dict, secret: str) -> str:
    """Lower immediately. A raise is proposed and approved with the existing HMAC."""
    coin_id = _field(fields, "coin")
    if coin_id not in COINS:
        return "onbekende coin"
    tx, day, auto_tx, auto_day = _integers(fields)
    cfg = load_config(env)
    if coin_id not in cfg.enabled_ids():
        return "coin staat uit"
    coin = cfg.coins[coin_id]
    notes: list[str] = []
    raising_limit = tx > coin.max_per_tx or day > coin.max_per_day
    server_secret = cfg.env.get("WALLET_MCP_APPROVAL_SECRET") or ""
    if raising_limit and not secret:
        notes.append("limietverhoging geweigerd: approval-secret ontbreekt")
    else:
        status, request = propose_limits(cfg, coin_id, tx, day)
        cfg = load_config(env)
        if status == "pending" and request is not None:
            # Code from what was typed; approve_limits compares it to the server secret
            # and seals limits_mac with that server secret.
            code = limit_approval_code(
                secret, request.request_id, request.coin, request.max_per_tx, request.max_per_day,
            )
            approve_limits(cfg, request.request_id, code, server_secret)
            notes.append("limiet verhoogd")
            cfg = load_config(env)
        elif status == "applied":
            notes.append("limiet verlaagd")
    coin = cfg.coins[coin_id]
    raising_auto = auto_tx > coin.auto_max_per_tx or auto_day > coin.auto_max_per_day
    if raising_auto and not secret:
        notes.append("budgetverhoging geweigerd: approval-secret ontbreekt")
    else:
        status, request = propose_auto_budget(cfg, coin_id, auto_tx, auto_day)
        if status == "pending" and request is not None:
            code = auto_approval_code(
                secret, request.request_id, request.coin, request.auto_max_per_tx, request.auto_max_per_day,
            )
            approve_auto_budget(cfg, request.request_id, code, server_secret)
            notes.append("autonoom budget verhoogd")
        elif status == "applied":
            notes.append("autonoom budget verlaagd")
    return " ".join(notes) or "ongewijzigd"


def approve_payment(env: dict, fields: dict, secret: str, runtime_factory) -> str:
    if not secret:
        return "goedkeuren geweigerd: approval-secret ontbreekt"
    coin_id = _field(fields, "coin")
    confirm_id = _field(fields, "confirm_id")
    if coin_id not in COINS or not _CONFIRM.fullmatch(confirm_id):
        return "onbekende betaling"
    cfg = load_config(env)
    if coin_id not in cfg.enabled_ids():
        return "coin staat uit"
    runtime, problem = _load_runtime(cfg, runtime_factory)
    if runtime is None or getattr(runtime, "gate", None) is None:
        return "goedkeuren geweigerd: " + (problem or "wallet niet geladen")
    policy = runtime.gate.policies.get(coin_id)
    if policy is None:
        return "onbekende betaling"
    pending = policy.get_pending(confirm_id)
    code = approval_code(
        secret, pending.coin, pending.network, pending.role, pending.confirm_id,
        pending.address, pending.amount, pending.fee, extra=pending.extra,
    )
    result = runtime.gate.send_payment(coin_id, confirm_id, code)
    if not result.get("ok"):
        return "goedkeuren geweigerd: " + str(result.get("error") or "mislukt")
    return "betaling goedgekeurd"


def reject_payment(env: dict, fields: dict) -> str:
    coin_id = _field(fields, "coin")
    confirm_id = _field(fields, "confirm_id")
    if coin_id not in COINS or not _CONFIRM.fullmatch(confirm_id):
        return "onbekende betaling"
    cfg = load_config(env)
    policies = _policies_for(cfg)
    policy = policies.get(coin_id)
    if policy is None:
        return "onbekende betaling"
    pending = policy.get_pending(confirm_id)
    policy.discard(confirm_id)
    AuditLog(cfg.data_dir / "audit.log").record(
        "approve_fail",
        coin=pending.coin,
        network=pending.network,
        confirm_id=pending.confirm_id,
        address=pending.address,
        amount=pending.amount,
        fee=pending.fee,
        ok=False,
        error="rejected",
    )
    return "betaling afgewezen"


def dispatch(env: dict, fields: dict, secret: str, runtime_factory) -> str:
    action = _field(fields, "action")
    try:
        if action == "limits":
            notice = apply_limit_form(env, fields, secret)
        elif action == "approve":
            notice = approve_payment(env, fields, secret, runtime_factory)
        elif action == "reject":
            notice = reject_payment(env, fields)
        else:
            notice = "onbekende actie"
    except (ConfigError, PolicyError, AdapterError) as exc:
        notice = str(exc)
    except Exception:
        notice = "de actie is mislukt"
    return _redact(notice, secret)


def make_dashboard(env=None, port: int = 8770, runtime_factory=None, price_fetcher=None) -> ThreadingHTTPServer:
    """Bind 127.0.0.1 only. Refuses ``WALLET_MCP_MODE=agent`` before listening."""
    import os
    import secrets

    env_snapshot = dict(os.environ if env is None else env)
    mode = (env_snapshot.get("WALLET_MCP_MODE") or "owner").strip().lower()
    if mode == "agent":
        raise SystemExit("refusing: the owner dashboard does not start when WALLET_MCP_MODE=agent")
    token = secrets.token_hex(16)
    factory = runtime_factory or _default_runtime
    fetcher = price_fetcher or _fetch_price_document
    price_cache = {"mono": 0.0, "value": {}}

    def prices_for(show_eur: bool) -> dict[str, float] | None:
        if not show_eur:
            return None
        now = time.monotonic()
        if price_cache["mono"] and now - price_cache["mono"] < 60:
            return price_cache["value"]
        loaded = load_eur_prices(fetcher)
        price_cache["mono"] = now
        price_cache["value"] = loaded
        return loaded

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _guarded(self, method):
            reason = client_refused(
                self.headers.get("Host"), self.headers.get("Origin"), self.server.server_address[1],
            )
            if reason:
                self._send(403, reason)
                return
            method()

        def _get(self):
            if self.path.split("?", 1)[0] != "/":
                self._send(404, "not found")
                return
            query = {}
            if "?" in self.path:
                query = parse_qs(self.path.split("?", 1)[1])
            notice = (query.get("notice") or [""])[0]
            show_eur = (query.get("eur") or [""])[0] == "1"
            try:
                cfg = load_config(env_snapshot)
            except ConfigError as exc:
                self._send(400, str(exc))
                return
            runtime, _problem = _load_runtime(cfg, factory)
            try:
                page = render_dashboard(cfg, token, runtime, prices_for(show_eur), notice, show_eur)
            except Exception as exc:
                self._send(500, "pagina kon niet worden opgebouwd: " + type(exc).__name__)
                return
            self._send(200, page, "text/html; charset=utf-8")

        def _post(self):
            if self.path.split("?", 1)[0] != "/":
                self._send(404, "not found")
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                self._send(413, "request too large")
                return
            try:
                raw = self.rfile.read(length).decode()
            except UnicodeDecodeError:
                self._send(400, "bad encoding")
                return
            fields = parse_qs(raw, keep_blank_values=True)
            if not csrf_matches(_field(fields, "csrf"), token):
                self._send(403, "bad csrf token")
                return
            secret = _field(fields, "approval_secret")
            fields["approval_secret"] = [""]
            notice = dispatch(env_snapshot, fields, secret, factory)
            show_eur = _field(fields, "eur") == "1"
            location = "/?notice=" + quote(notice, safe="")
            if show_eur:
                location += "&eur=1"
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self._headers()
            self.end_headers()

        def log_message(self, fmt, *args):
            return

        def log_error(self, fmt, *args):
            return

        def _headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            # same-origin: no-referrer makes Chrome and Edge send Origin: null on form POST.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", _CSP)

        def _send(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8"):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self._headers()
            self.end_headers()
            self.wfile.write(data)

    # Localhost only. There is no host argument that can widen this.
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd
