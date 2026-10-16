"""Per-coin configuration file plus environment secrets.

The file (``config.json``) holds which coins are enabled, the network, and the
limits. Mainnet is not a file setting by itself: an enabled mainnet coin also
needs ``WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`` in the
environment. Limit ceilings above the built-in defaults need a human HMAC.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from .coins import COIN_ORDER, COINS, TOKEN_PARENTS, CoinSpec, mainnet_opted_in
from .disclaimer import MAINNET_WARNING
from btc_wallet_mcp.guard import MAX_UNIT

from .policy import (
    AutoBudgetRequest,
    LimitRequest,
    PolicyError,
    auto_approval_code,
    auto_mac,
    limit_approval_code,
    limits_mac,
)
from .util import atomic_json, ensure_dir

LIMIT_REQUESTS = "limit-requests.json"
AUTO_REQUESTS = "auto-requests.json"
LIMIT_TTL = 86_400


class ConfigError(Exception):
    pass


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def _nonneg_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{label} must be an integer >= 0")
    return value


@dataclass(frozen=True)
class CoinConfig:
    spec: CoinSpec
    enabled: bool
    network: str
    max_per_tx: int
    max_per_day: int
    max_fee_rate: int
    allowlist: frozenset[str]
    approved_max_per_tx: int
    approved_max_per_day: int
    limits_mac: str
    auto_max_per_tx: int
    auto_max_per_day: int
    auto_mac: str

    @property
    def coin_id(self) -> str:
        return self.spec.coin_id

    @property
    def is_mainnet(self) -> bool:
        return self.spec.networks[self.network].mainnet

    @property
    def chain_id(self) -> int | None:
        return self.spec.networks[self.network].chain_id


@dataclass
class AppConfig:
    path: Path
    data_dir: Path
    mode: str
    coins: dict[str, CoinConfig]
    raw: dict
    env: dict = field(repr=False)

    def coin_root(self, coin_id: str) -> Path:
        return self.data_dir / coin_id / self.coins[coin_id].network

    def hot_dir(self, coin_id: str) -> Path:
        return self.coin_root(coin_id) / "hot"

    def cold_dir(self, coin_id: str) -> Path:
        return self.coin_root(coin_id) / "cold"

    def policy_dir(self, coin_id: str) -> Path:
        return self.coin_root(coin_id) / "policy"

    def enabled_ids(self) -> list[str]:
        return [coin_id for coin_id in COIN_ORDER if self.coins[coin_id].enabled]


def default_raw() -> dict:
    coins = {}
    for coin_id in COIN_ORDER:
        spec = COINS[coin_id]
        coins[coin_id] = {
            "enabled": False,
            "network": spec.default_network,
            "max_per_tx": spec.default_max_per_tx,
            "max_per_day": spec.default_max_per_day,
            "max_fee_rate": spec.default_max_fee_rate,
            "allowlist": [],
            "approved_max_per_tx": spec.default_max_per_tx,
            "approved_max_per_day": spec.default_max_per_day,
            "limits_mac": "",
            "auto_max_per_tx": 0,
            "auto_max_per_day": 0,
            "auto_mac": "",
        }
    return {"version": 1, "coins": coins}


def config_paths(env=None) -> tuple[Path, Path]:
    env = os.environ if env is None else env
    data = Path(env.get("WALLET_MCP_DATA_DIR") or Path.home() / ".wallet-mcp")
    path = Path(env.get("WALLET_MCP_CONFIG") or data / "config.json")
    return data, path


def _merge_raw(document: dict) -> dict:
    base = default_raw()
    incoming = document.get("coins") if isinstance(document, dict) else None
    if not isinstance(incoming, dict):
        incoming = {}
    for coin_id in COIN_ORDER:
        current = incoming.get(coin_id) or {}
        if not isinstance(current, dict):
            raise ConfigError(f"{coin_id} config must be an object")
        slot = base["coins"][coin_id]
        if "enabled" in current:
            slot["enabled"] = bool(current["enabled"])
        if current.get("network"):
            slot["network"] = str(current["network"])
        for key in ("max_per_tx", "max_per_day", "max_fee_rate",
                    "approved_max_per_tx", "approved_max_per_day",
                    "auto_max_per_tx", "auto_max_per_day"):
            if key in current and current[key] is not None:
                slot[key] = current[key]
        if "allowlist" in current and current["allowlist"] is not None:
            if not isinstance(current["allowlist"], list):
                raise ConfigError(f"{coin_id} allowlist must be a list")
            slot["allowlist"] = [str(item).strip() for item in current["allowlist"] if str(item).strip()]
        if current.get("limits_mac"):
            slot["limits_mac"] = str(current["limits_mac"])
        if current.get("auto_mac"):
            slot["auto_mac"] = str(current["auto_mac"])
    return base


def _verify_limits(coin_id: str, slot: dict, secret: str | None) -> None:
    spec = COINS[coin_id]
    tx = _positive_int(slot["max_per_tx"], f"{coin_id} max_per_tx")
    day = _positive_int(slot["max_per_day"], f"{coin_id} max_per_day")
    fee = _positive_int(slot["max_fee_rate"], f"{coin_id} max_fee_rate")
    approved_tx = _positive_int(slot["approved_max_per_tx"], f"{coin_id} approved_max_per_tx")
    approved_day = _positive_int(slot["approved_max_per_day"], f"{coin_id} approved_max_per_day")
    slot["max_per_tx"], slot["max_per_day"], slot["max_fee_rate"] = tx, day, fee
    slot["approved_max_per_tx"], slot["approved_max_per_day"] = approved_tx, approved_day
    if tx > approved_tx or day > approved_day:
        raise ConfigError(f"{coin_id}: limits exceed the approved ceiling")
    raised = approved_tx > spec.default_max_per_tx or approved_day > spec.default_max_per_day
    if raised:
        mac = slot.get("limits_mac") or ""
        try:
            expected = limits_mac(secret or "", coin_id, approved_tx, approved_day)
        except PolicyError:
            expected = ""
        if not mac or not expected or not hmac_equal(expected, mac):
            raise ConfigError(
                f"{coin_id}: limit increase is not approved; run `wallet-mcp approve-limit`"
            )
    _verify_auto(coin_id, slot, secret, tx, day)


def _verify_auto(coin_id: str, slot: dict, secret: str | None, tx: int, day: int) -> None:
    """A non-zero autonomous budget must match ``auto_mac`` and fit under the hard limits.

    Zero and zero is the default and needs no MAC. Any other pair is spendable
    authority, so a missing or mismatched MAC fails closed.
    """
    auto_tx = _nonneg_int(slot["auto_max_per_tx"], f"{coin_id} auto_max_per_tx")
    auto_day = _nonneg_int(slot["auto_max_per_day"], f"{coin_id} auto_max_per_day")
    if auto_tx > MAX_UNIT or auto_day > MAX_UNIT:
        raise ConfigError(f"{coin_id}: auto budget is too large")
    slot["auto_max_per_tx"], slot["auto_max_per_day"] = auto_tx, auto_day
    if auto_tx > tx or auto_day > day:
        raise ConfigError(f"{coin_id}: auto budget exceeds max_per_tx / max_per_day")
    if auto_tx == 0 and auto_day == 0:
        return
    mac = slot.get("auto_mac") or ""
    try:
        expected = auto_mac(secret or "", coin_id, auto_tx, auto_day)
    except PolicyError:
        expected = ""
    if not mac or not expected or not hmac_equal(expected, mac):
        raise ConfigError(
            f"{coin_id}: auto budget is not approved; run `wallet-mcp approve-auto`"
        )


def hmac_equal(expected: str, given: str) -> bool:
    from btc_wallet_mcp.guard import constant_time_equals
    return constant_time_equals(expected, (given or "").strip().lower())


def load_config(env=None, *, require_mainnet_flag: bool = True) -> AppConfig:
    env = dict(os.environ if env is None else env)
    data_dir, path = config_paths(env)
    if path.exists():
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"cannot parse {path}") from exc
    else:
        document = default_raw()
    raw = _merge_raw(document)
    secret = env.get("WALLET_MCP_APPROVAL_SECRET")
    mode = (env.get("WALLET_MCP_MODE") or "owner").strip().lower()
    if mode not in {"owner", "agent"}:
        raise ConfigError("WALLET_MCP_MODE must be owner or agent")
    coins: dict[str, CoinConfig] = {}
    for coin_id in COIN_ORDER:
        spec = COINS[coin_id]
        slot = raw["coins"][coin_id]
        try:
            network = spec.canonical_network(slot["network"])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        slot["network"] = network
        _verify_limits(coin_id, slot, secret)
        cfg = CoinConfig(
            spec=spec,
            enabled=bool(slot["enabled"]),
            network=network,
            max_per_tx=slot["max_per_tx"],
            max_per_day=slot["max_per_day"],
            max_fee_rate=slot["max_fee_rate"],
            allowlist=frozenset(slot["allowlist"]),
            approved_max_per_tx=slot["approved_max_per_tx"],
            approved_max_per_day=slot["approved_max_per_day"],
            limits_mac=slot["limits_mac"],
            auto_max_per_tx=slot["auto_max_per_tx"],
            auto_max_per_day=slot["auto_max_per_day"],
            auto_mac=slot.get("auto_mac") or "",
        )
        if require_mainnet_flag and cfg.enabled and cfg.is_mainnet and not mainnet_opted_in(env, coin_id):
            raise ConfigError(f"{coin_id} mainnet requires {_mainnet_hint(coin_id)}")
        coins[coin_id] = cfg
    return AppConfig(path=path, data_dir=data_dir, mode=mode, coins=coins, raw=raw, env=env)


def _mainnet_hint(coin_id: str) -> str:
    from .coins import allow_mainnet_var, MAINNET_OPT_IN
    return f"{allow_mainnet_var(coin_id)}={MAINNET_OPT_IN}"


def save_raw(path: Path, raw: dict) -> None:
    ensure_dir(path.parent)
    atomic_json(path, raw)


def save_config(cfg: AppConfig) -> None:
    save_raw(cfg.path, cfg.raw)


def apply_enabled(cfg: AppConfig, enabled: set[str]) -> list[str]:
    """Write the checkbox selection. Returns human-readable warnings.

    Refuses to enable an unknown coin, or a mainnet coin without that coin's
    opt-in flag. Does not change limits.
    """
    unknown = sorted(set(enabled) - set(COINS))
    if unknown:
        raise ConfigError("unknown coin: " + ", ".join(unknown))
    warnings: list[str] = []
    for coin_id in COIN_ORDER:
        spec = COINS[coin_id]
        slot = cfg.raw["coins"][coin_id]
        turn_on = coin_id in enabled
        parent = TOKEN_PARENTS.get(coin_id)
        if turn_on and parent and parent not in enabled:
            raise ConfigError(f"{coin_id} uses the {parent} wallet; enable {parent} as well")
        if turn_on and COINS[coin_id].networks[slot["network"]].mainnet and not mainnet_opted_in(cfg.env, coin_id):
            raise ConfigError(
                f"refusing to enable {coin_id} on mainnet without {_mainnet_hint(coin_id)}"
            )
        slot["enabled"] = turn_on
        if turn_on and spec.experimental:
            warnings.append(f"{spec.name} is experimental")
        if turn_on and COINS[coin_id].networks[slot["network"]].mainnet:
            warnings.append(f"{coin_id}: {MAINNET_WARNING}")
    save_config(cfg)
    return warnings


def propose_limits(cfg: AppConfig, coin_id: str, max_per_tx: int, max_per_day: int, now=time.time):
    """Lower limits immediately. Any increase becomes a pending human approval.

    Returns ``("applied", None)``, ``("unchanged", None)`` or ``("pending", request)``.
    """
    if coin_id not in COINS:
        raise ConfigError(f"unknown coin {coin_id}")
    max_per_tx = _positive_int(max_per_tx, "max_per_tx")
    max_per_day = _positive_int(max_per_day, "max_per_day")
    slot = cfg.raw["coins"][coin_id]
    current_tx = int(slot["max_per_tx"])
    current_day = int(slot["max_per_day"])
    if max_per_tx == current_tx and max_per_day == current_day:
        return "unchanged", None
    if max_per_tx <= current_tx and max_per_day <= current_day:
        slot["max_per_tx"] = max_per_tx
        slot["max_per_day"] = max_per_day
        _clamp_auto_to_limits(cfg, coin_id)
        save_config(cfg)
        return "applied", None
    request = LimitRequest("lim_" + secrets.token_hex(8), coin_id, max_per_tx, max_per_day, now())
    _write_limit_request(cfg, request)
    return "pending", request


def _requests_path(cfg: AppConfig) -> Path:
    return cfg.data_dir / LIMIT_REQUESTS


def _read_requests(cfg: AppConfig) -> dict:
    path = _requests_path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_limit_request(cfg: AppConfig, request: LimitRequest) -> None:
    blob = _read_requests(cfg)
    blob[request.request_id] = {
        "request_id": request.request_id,
        "coin": request.coin,
        "max_per_tx": request.max_per_tx,
        "max_per_day": request.max_per_day,
        "created": request.created,
    }
    atomic_json(_requests_path(cfg), blob)


def get_limit_request(cfg: AppConfig, request_id: str, now=time.time) -> LimitRequest:
    blob = _read_requests(cfg)
    if request_id not in blob:
        raise ConfigError("unknown limit request")
    request = LimitRequest(**blob[request_id])
    if now() - request.created > LIMIT_TTL:
        raise ConfigError("limit request expired")
    return request


def approve_limits(cfg: AppConfig, request_id: str, code: str, secret: str | None) -> LimitRequest:
    """Apply a pending increase after the human presents the matching code."""
    request = get_limit_request(cfg, request_id)
    if not secret:
        raise ConfigError("WALLET_MCP_APPROVAL_SECRET is not set")
    expected = limit_approval_code(secret, request.request_id, request.coin, request.max_per_tx, request.max_per_day)
    if not hmac_equal(expected, (code or "").strip().lower()):
        raise ConfigError("invalid limit approval code")
    slot = cfg.raw["coins"][request.coin]
    slot["max_per_tx"] = request.max_per_tx
    slot["max_per_day"] = request.max_per_day
    approved_tx = max(int(slot["approved_max_per_tx"]), request.max_per_tx)
    approved_day = max(int(slot["approved_max_per_day"]), request.max_per_day)
    slot["approved_max_per_tx"] = approved_tx
    slot["approved_max_per_day"] = approved_day
    spec = COINS[request.coin]
    if approved_tx > spec.default_max_per_tx or approved_day > spec.default_max_per_day:
        slot["limits_mac"] = limits_mac(secret, request.coin, approved_tx, approved_day)
    else:
        slot["limits_mac"] = ""
    _clamp_auto_to_limits(cfg, request.coin)
    save_config(cfg)
    blob = _read_requests(cfg)
    blob.pop(request_id, None)
    atomic_json(_requests_path(cfg), blob)
    return request


def _seal_auto(slot: dict, coin_id: str, auto_tx: int, auto_day: int, secret: str | None) -> None:
    slot["auto_max_per_tx"] = auto_tx
    slot["auto_max_per_day"] = auto_day
    if auto_tx == 0 and auto_day == 0:
        slot["auto_mac"] = ""
        return
    if not secret:
        raise ConfigError("WALLET_MCP_APPROVAL_SECRET is not set")
    slot["auto_mac"] = auto_mac(secret, coin_id, auto_tx, auto_day)


def _clamp_auto_to_limits(cfg: AppConfig, coin_id: str) -> None:
    """Keep the autonomous budget inside the hard limits.

    Lowering is the safe direction. When the approval secret is available the
    new pair is resealed. When it is not, the budget is turned off so a file
    with a stale MAC still loads.
    """
    slot = cfg.raw["coins"][coin_id]
    auto_tx = min(int(slot.get("auto_max_per_tx") or 0), int(slot["max_per_tx"]))
    auto_day = min(int(slot.get("auto_max_per_day") or 0), int(slot["max_per_day"]))
    if auto_tx == int(slot.get("auto_max_per_tx") or 0) and auto_day == int(slot.get("auto_max_per_day") or 0):
        return
    secret = cfg.env.get("WALLET_MCP_APPROVAL_SECRET")
    if auto_tx > 0 or auto_day > 0:
        if not secret:
            auto_tx, auto_day = 0, 0
    _seal_auto(slot, coin_id, auto_tx, auto_day, secret)


def propose_auto_budget(cfg: AppConfig, coin_id: str, auto_tx: int, auto_day: int, now=time.time):
    """Lower an autonomous budget immediately. Any increase becomes a pending human approval.

    Returns ``("applied", None)``, ``("unchanged", None)`` or ``("pending", request)``.
    The budget has to fit under ``max_per_tx`` and ``max_per_day``. There is no
    price conversion: both numbers are base units.
    """
    if coin_id not in COINS:
        raise ConfigError(f"unknown coin {coin_id}")
    auto_tx = _nonneg_int(auto_tx, "auto_max_per_tx")
    auto_day = _nonneg_int(auto_day, "auto_max_per_day")
    if auto_tx > MAX_UNIT or auto_day > MAX_UNIT:
        raise ConfigError("auto budget is too large")
    slot = cfg.raw["coins"][coin_id]
    max_tx = int(slot["max_per_tx"])
    max_day = int(slot["max_per_day"])
    if auto_tx > max_tx or auto_day > max_day:
        raise ConfigError(
            "auto budget must be <= max_per_tx / max_per_day; "
            "raise that limit first with `wallet-mcp request-limit`"
        )
    current_tx = int(slot.get("auto_max_per_tx") or 0)
    current_day = int(slot.get("auto_max_per_day") or 0)
    if auto_tx == current_tx and auto_day == current_day:
        return "unchanged", None
    if auto_tx <= current_tx and auto_day <= current_day:
        _seal_auto(slot, coin_id, auto_tx, auto_day, cfg.env.get("WALLET_MCP_APPROVAL_SECRET"))
        save_config(cfg)
        return "applied", None
    request = AutoBudgetRequest("aut_" + secrets.token_hex(8), coin_id, auto_tx, auto_day, now())
    _write_auto_request(cfg, request)
    return "pending", request


def _auto_requests_path(cfg: AppConfig) -> Path:
    return cfg.data_dir / AUTO_REQUESTS


def _read_auto_requests(cfg: AppConfig) -> dict:
    path = _auto_requests_path(cfg)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_auto_request(cfg: AppConfig, request: AutoBudgetRequest) -> None:
    blob = _read_auto_requests(cfg)
    blob[request.request_id] = {
        "request_id": request.request_id,
        "coin": request.coin,
        "auto_max_per_tx": request.auto_max_per_tx,
        "auto_max_per_day": request.auto_max_per_day,
        "created": request.created,
    }
    ensure_dir(cfg.data_dir)
    atomic_json(_auto_requests_path(cfg), blob)


def get_auto_request(cfg: AppConfig, request_id: str, now=time.time) -> AutoBudgetRequest:
    blob = _read_auto_requests(cfg)
    if request_id not in blob:
        raise ConfigError("unknown auto budget request")
    request = AutoBudgetRequest(**blob[request_id])
    if now() - request.created > LIMIT_TTL:
        raise ConfigError("auto budget request expired")
    return request


def approve_auto_budget(cfg: AppConfig, request_id: str, code: str, secret: str | None) -> AutoBudgetRequest:
    """Apply a pending autonomous-budget increase after the human presents the matching code."""
    request = get_auto_request(cfg, request_id)
    if not secret:
        raise ConfigError("WALLET_MCP_APPROVAL_SECRET is not set")
    expected = auto_approval_code(
        secret, request.request_id, request.coin, request.auto_max_per_tx, request.auto_max_per_day,
    )
    if not hmac_equal(expected, (code or "").strip().lower()):
        raise ConfigError("invalid auto budget approval code")
    slot = cfg.raw["coins"][request.coin]
    max_tx = int(slot["max_per_tx"])
    max_day = int(slot["max_per_day"])
    if request.auto_max_per_tx > max_tx or request.auto_max_per_day > max_day:
        raise ConfigError("auto budget must be <= max_per_tx / max_per_day")
    _seal_auto(slot, request.coin, request.auto_max_per_tx, request.auto_max_per_day, secret)
    save_config(cfg)
    blob = _read_auto_requests(cfg)
    blob.pop(request_id, None)
    atomic_json(_auto_requests_path(cfg), blob)
    return request
