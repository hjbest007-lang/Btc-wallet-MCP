"""Per-coin spending policy. Adapters do not see this module.

Every coin goes through the same checks: positive amount, optional allowlist,
per-tx limit, rolling 24h limit (amount + fee), human HMAC, single use, expiry,
and lockout after repeated bad codes. The HMAC binds coin, network and role, so
a code for one coin cannot approve another.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .util import atomic_json

DAY = 86_400
MIN_APPROVAL_SECRET_LEN = 16


class PolicyError(Exception):
    pass


@dataclass
class Pending:
    confirm_id: str
    coin: str
    network: str
    role: str
    address: str
    amount: int
    fee: int
    memo: str
    payload: str
    created: float
    extra: str = ""


@dataclass
class LimitRequest:
    request_id: str
    coin: str
    max_per_tx: int
    max_per_day: int
    created: float


def _hmac(secret: str, msg: bytes) -> str:
    if not secret or len(secret) < MIN_APPROVAL_SECRET_LEN:
        raise PolicyError("approval secret too short")
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def approval_code(
    secret: str,
    coin: str,
    network: str,
    role: str,
    confirm_id: str,
    address: str,
    amount: int,
    fee: int,
    extra: str = "",
) -> str:
    """12-hex code a human computes to authorise exactly one hot-wallet payment.

    ``extra`` binds an on-chain field such as an XRP destination tag. An empty
    extra keeps the historical message, so a code for a payment with no tag
    still matches. A code that omits the tag does not match a tagged payment.
    """
    msg = f"{coin}|{network}|{role}|{confirm_id}|{address}|{amount}|{fee}"
    if extra:
        msg = f"{msg}|{extra}"
    return _hmac(secret, msg.encode())[:12]


def limit_approval_code(secret: str, request_id: str, coin: str, max_per_tx: int, max_per_day: int) -> str:
    msg = f"limit|{request_id}|{coin}|{max_per_tx}|{max_per_day}".encode()
    return _hmac(secret, msg)[:12]


def limits_mac(secret: str, coin: str, approved_tx: int, approved_day: int) -> str:
    """Full HMAC stored next to a raised ceiling. The agent cannot forge it."""
    msg = f"limits|{coin}|{approved_tx}|{approved_day}".encode()
    return _hmac(secret, msg)


def _matches(expected: str, given: str) -> bool:
    candidate = (given or "").strip().lower()
    if len(candidate) != len(expected):
        return False
    return hmac.compare_digest(expected, candidate)


class Policy:
    def __init__(self, coin: str, network: str, unit: str, directory: Path, limits, now=time.time,
                 ttl: int = 600, max_failed: int = 5):
        self.coin = coin
        self.network = network
        self.unit = unit
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits = limits  # () -> (max_per_tx, max_per_day, allowlist)
        self.now = now
        self.ttl = ttl
        self.max_failed = max_failed
        self.ledger_path = self.directory / "ledger.json"
        self.state_path = self.directory / "pending.json"

    def _ledger(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        return json.loads(self.ledger_path.read_text())

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"pending": {}, "failed": 0}

    def spent_last_24h(self) -> int:
        cutoff = self.now() - DAY
        return sum(entry["total"] for entry in self._ledger() if entry["ts"] >= cutoff)

    def _caps(self) -> tuple[int, int, frozenset[str]]:
        max_tx, max_day, allow = self.limits()
        return max_tx, max_day, frozenset(allow)

    def check_request(self, address: str, amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise PolicyError(f"amount must be a positive integer ({self.unit})")
        _max_tx, _max_day, allow = self._caps()
        if allow and address not in allow:
            raise PolicyError("address not on allowlist")
        if amount > _max_tx:
            raise PolicyError(f"amount exceeds per-tx limit ({_max_tx} {self.unit})")

    def check_total(self, amount: int, fee: int) -> None:
        if not isinstance(fee, int) or isinstance(fee, bool) or fee < 0:
            raise PolicyError("fee must be a non-negative integer")
        total = amount + fee
        max_tx, max_day, _allow = self._caps()
        if total > max_tx:
            raise PolicyError(f"amount+fee exceeds per-tx limit ({max_tx} {self.unit})")
        spent = self.spent_last_24h()
        if spent + total > max_day:
            raise PolicyError(
                f"daily limit: {spent} spent in last 24h, limit {max_day} {self.unit}"
            )

    def add_pending(self, address, amount, fee, memo, payload, extra: str = "") -> Pending:
        state = self._state()
        self._expire(state)
        pending = Pending(
            "pay_" + secrets.token_hex(8),
            self.coin,
            self.network,
            "hot",
            address,
            amount,
            fee,
            memo,
            payload,
            self.now(),
            extra or "",
        )
        state["pending"][pending.confirm_id] = asdict(pending)
        atomic_json(self.state_path, state)
        return pending

    def _expire(self, state) -> None:
        cutoff = self.now() - self.ttl
        state["pending"] = {
            key: value for key, value in state["pending"].items() if value["created"] >= cutoff
        }

    def get_pending(self, confirm_id: str) -> Pending:
        state = self._state()
        self._expire(state)
        atomic_json(self.state_path, state)
        if confirm_id not in state["pending"]:
            raise PolicyError("unknown or expired confirm_id")
        return Pending(**state["pending"][confirm_id])

    def verify_approval(self, pending: Pending, code: str, secret: str | None) -> None:
        if not secret:
            raise PolicyError("server has no WALLET_MCP_APPROVAL_SECRET; sending disabled")
        state = self._state()
        if state["failed"] >= self.max_failed:
            raise PolicyError(
                "locked after too many bad approval codes; human must run `wallet-mcp unlock --coin "
                + self.coin + "`"
            )
        expected = approval_code(
            secret, pending.coin, pending.network, pending.role, pending.confirm_id,
            pending.address, pending.amount, pending.fee, extra=pending.extra,
        )
        if not _matches(expected, code):
            state["failed"] += 1
            atomic_json(self.state_path, state)
            raise PolicyError("invalid approval code")
        state["failed"] = 0
        atomic_json(self.state_path, state)

    def consume(self, pending: Pending, txid: str) -> None:
        state = self._state()
        state["pending"].pop(pending.confirm_id, None)
        atomic_json(self.state_path, state)
        ledger = self._ledger()
        ledger.append({
            "ts": self.now(),
            "txid": txid,
            "address": pending.address,
            "total": pending.amount + pending.fee,
            "memo": pending.memo,
            "coin": self.coin,
        })
        atomic_json(self.ledger_path, ledger)

    def discard(self, confirm_id: str) -> None:
        state = self._state()
        state["pending"].pop(confirm_id, None)
        atomic_json(self.state_path, state)

    def unlock(self) -> None:
        state = self._state()
        state["failed"] = 0
        atomic_json(self.state_path, state)
