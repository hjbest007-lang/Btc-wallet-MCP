"""Per-coin spending policy. Adapters do not see this module.

Every coin goes through the same checks: positive amount, optional allowlist,
per-tx limit, rolling 24h limit (amount + fee), human HMAC, single use, expiry,
and lockout after repeated bad codes. The HMAC binds coin, network and role, so
a code for one coin cannot approve another.

An owner-granted autonomous budget is a second counter on the same ledger.
Payments that fit ``auto_max_per_tx`` and the remaining ``auto_max_per_day``
are reserved under the same file lock and may be broadcast without a
per-payment code. They still count toward the hard daily limit. Anything
above the budget keeps the human HMAC. The budget is an integer in the coin's
base unit; this module does not consult a price.

The 24h ledger and the lockout counter are sealed with the approval secret when
one is configured. A writer who does not have the secret cannot zero the spend
or clear the lockout without the next spend failing closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from btc_wallet_mcp.guard import (
    MAX_UNIT,
    MIN_APPROVAL_SECRET_LEN,
    GuardError,
    assert_safe_address,
    assert_safe_memo,
    exclusive_lock,
    seal,
    unseal,
)

from .util import atomic_json

DAY = 86_400


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


@dataclass
class AutoBudgetRequest:
    request_id: str
    coin: str
    auto_max_per_tx: int
    auto_max_per_day: int
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


def hardware_approval_code(
    secret: str, coin: str, network: str, address: str, amount: int, fee: int, payload_hash: str,
) -> str:
    """Code for one cold hardware signature. It is not a hot-wallet send code.

    The domain prefix ``hw`` and the role ``cold`` keep it from matching
    ``approval_code``. The payload hash binds the PSBT or EIP-712 document.
    """
    msg = f"hw|{coin}|{network}|cold|{address}|{amount}|{fee}|{payload_hash}".encode()
    return _hmac(secret, msg)[:12]


def limit_approval_code(secret: str, request_id: str, coin: str, max_per_tx: int, max_per_day: int) -> str:
    msg = f"limit|{request_id}|{coin}|{max_per_tx}|{max_per_day}".encode()
    return _hmac(secret, msg)[:12]


def limits_mac(secret: str, coin: str, approved_tx: int, approved_day: int) -> str:
    """Full HMAC stored next to a raised ceiling. The agent cannot forge it."""
    msg = f"limits|{coin}|{approved_tx}|{approved_day}".encode()
    return _hmac(secret, msg)


def auto_approval_code(secret: str, request_id: str, coin: str, auto_tx: int, auto_day: int) -> str:
    """12-hex code for one queued autonomous-budget increase. Not a payment code."""
    msg = f"auto|{request_id}|{coin}|{auto_tx}|{auto_day}".encode()
    return _hmac(secret, msg)[:12]


def auto_mac(secret: str, coin: str, auto_tx: int, auto_day: int) -> str:
    """Full HMAC stored next to a non-zero autonomous budget. The agent cannot forge it."""
    msg = f"automac|{coin}|{auto_tx}|{auto_day}".encode()
    return _hmac(secret, msg)


def _matches(expected: str, given: str) -> bool:
    from btc_wallet_mcp.guard import constant_time_equals

    return constant_time_equals(expected, given)


class Policy:
    def __init__(
        self,
        coin: str,
        network: str,
        unit: str,
        directory: Path,
        limits: Callable[[], tuple[int, int, frozenset[str]]],
        now: Callable[[], float] = time.time,
        ttl: int = 600,
        max_failed: int = 5,
        secret_provider: Callable[[], str | None] | None = None,
        auto_limits: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.coin = coin
        self.network = network
        self.unit = unit
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits = limits
        self.auto_limits = auto_limits
        self.now = now
        self.ttl = ttl
        self.max_failed = max_failed
        self.secret_provider = secret_provider
        self.ledger_path = self.directory / "ledger.json"
        self.state_path = self.directory / "pending.json"

    def _secret(self) -> str | None:
        if self.secret_provider is None:
            return None
        value = self.secret_provider()
        if not value or len(value) < MIN_APPROVAL_SECRET_LEN:
            return None
        return value

    def _domain(self, kind: str) -> str:
        return f"wallet|{self.coin}|{self.network}|{kind}"

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text())

    def _write_sealed(self, path: Path, domain: str, body: dict[str, Any]) -> None:
        secret = self._secret()
        payload = dict(body)
        if secret:
            payload["mac"] = seal(secret, domain, body)
        atomic_json(path, payload)

    def _ledger_entries(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        raw = self._read_json(self.ledger_path)
        secret = self._secret()
        if isinstance(raw, list):
            if secret:
                raise PolicyError("ledger integrity check failed")
            return cast(list[dict[str, Any]], raw)
        if not isinstance(raw, dict):
            raise PolicyError("ledger integrity check failed")
        if secret:
            try:
                raw = unseal(secret, self._domain("ledger"), raw)
            except GuardError as exc:
                raise PolicyError("ledger integrity check failed") from exc
        else:
            raw.pop("mac", None)
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise PolicyError("ledger integrity check failed")
        return cast(list[dict[str, Any]], entries)

    def _write_ledger(self, entries: list[dict[str, Any]]) -> None:
        self._write_sealed(self.ledger_path, self._domain("ledger"), {"entries": entries})

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"pending": {}, "failed": 0}
        raw = self._read_json(self.state_path)
        if not isinstance(raw, dict):
            raise PolicyError("policy state failed integrity check; run unlock")
        secret = self._secret()
        if secret:
            try:
                raw = unseal(secret, self._domain("state"), raw)
            except GuardError as exc:
                raise PolicyError("policy state failed integrity check; run unlock") from exc
        else:
            raw.pop("mac", None)
        pending = raw.get("pending")
        failed = raw.get("failed", 0)
        if not isinstance(pending, dict) or isinstance(failed, bool) or not isinstance(failed, int) or failed < 0:
            raise PolicyError("policy state failed integrity check; run unlock")
        return {"pending": pending, "failed": failed}

    def _write_state(self, state: dict[str, Any]) -> None:
        body = {"pending": state["pending"], "failed": state["failed"]}
        self._write_sealed(self.state_path, self._domain("state"), body)

    def spent_last_24h(self) -> int:
        with exclusive_lock(self.directory):
            return self._spent()

    def auto_spent_last_24h(self) -> int:
        with exclusive_lock(self.directory):
            return self._auto_spent()

    def _entry_parts(self, entry: object) -> tuple[float, int, bool]:
        if not isinstance(entry, dict):
            raise PolicyError("ledger integrity check failed")
        ts = entry.get("ts")
        amount = entry.get("total")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise PolicyError("ledger integrity check failed")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or amount > MAX_UNIT:
            raise PolicyError("ledger integrity check failed")
        flag = entry.get("auto", False)
        if not isinstance(flag, bool):
            raise PolicyError("ledger integrity check failed")
        return float(ts), amount, flag

    def _spent(self) -> int:
        return self._sum(auto_only=False)

    def _auto_spent(self) -> int:
        return self._sum(auto_only=True)

    def _sum(self, *, auto_only: bool) -> int:
        cutoff = self.now() - DAY
        total = 0
        for entry in self._ledger_entries():
            ts, amount, is_auto = self._entry_parts(entry)
            # A backdated timestamp would hide a spend. Sealing stops a writer
            # without the secret from doing that. Future timestamps still fall
            # inside ``ts >= cutoff`` and therefore still count.
            if ts >= cutoff and (not auto_only or is_auto):
                total += amount
        return total

    def _caps(self) -> tuple[int, int, frozenset[str]]:
        max_tx, max_day, allow = self.limits()
        return max_tx, max_day, frozenset(allow)

    def _auto_caps(self) -> tuple[int, int]:
        if self.auto_limits is None:
            return 0, 0
        try:
            found = self.auto_limits()
        except PolicyError:
            raise
        except Exception as exc:
            raise PolicyError(str(exc) or "auto budget failed integrity check") from exc
        if not isinstance(found, tuple) or len(found) != 2:
            raise PolicyError("auto budget failed integrity check")
        auto_tx, auto_day = found
        for value in (auto_tx, auto_day):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_UNIT:
                raise PolicyError("auto budget failed integrity check")
        return auto_tx, auto_day

    def _within_auto(self, total: int) -> bool:
        """True when ``total`` fits the owner-granted budget. Zero means the feature is off."""
        auto_tx, auto_day = self._auto_caps()
        if auto_tx < 1 or auto_day < 1:
            return False
        if total > auto_tx:
            return False
        return self._auto_spent() + total <= auto_day

    def _bounded(self, value: object, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value > MAX_UNIT:
            raise PolicyError(f"{label} must be an integer")
        return value

    def check_request(self, address: str, amount: int) -> str:
        try:
            address = assert_safe_address(address)
        except GuardError as exc:
            raise PolicyError(str(exc)) from None
        amount = self._bounded(amount, "amount")
        if amount <= 0:
            raise PolicyError(f"amount must be a positive integer ({self.unit})")
        _max_tx, _max_day, allow = self._caps()
        if allow and address not in allow:
            raise PolicyError("address not on allowlist")
        if amount > _max_tx:
            raise PolicyError(f"amount exceeds per-tx limit ({_max_tx} {self.unit})")
        return address

    def check_total(self, amount: int, fee: int) -> None:
        with exclusive_lock(self.directory):
            self._check_total(amount, fee)

    def _check_total(self, amount: int, fee: int) -> None:
        amount = self._bounded(amount, "amount")
        fee = self._bounded(fee, "fee")
        if amount < 0 or (amount == 0 and fee <= 0):
            raise PolicyError(f"amount must be a positive integer ({self.unit})")
        if fee < 0:
            raise PolicyError("fee must be a non-negative integer")
        total = amount + fee
        max_tx, max_day, _allow = self._caps()
        if total > max_tx:
            raise PolicyError(f"amount+fee exceeds per-tx limit ({max_tx} {self.unit})")
        if self._spent() + total > max_day:
            spent = self._spent()
            raise PolicyError(f"daily limit: {spent} spent in last 24h, limit {max_day} {self.unit}")

    def add_pending(self, address, amount, fee, memo, payload, extra: str = "") -> Pending:
        with exclusive_lock(self.directory):
            return self._add_pending(address, amount, fee, memo, payload, extra)

    def _add_pending(self, address: str, amount: int, fee: int, memo: str, payload: str, extra: str = "") -> Pending:
        try:
            memo = assert_safe_memo(memo)
        except GuardError as exc:
            raise PolicyError(str(exc)) from None
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
        self._write_state(state)
        return pending

    def _expire(self, state: dict[str, Any]) -> None:
        cutoff = self.now() - self.ttl
        state["pending"] = {
            key: value for key, value in state["pending"].items() if value["created"] >= cutoff
        }

    def get_pending(self, confirm_id: str) -> Pending:
        with exclusive_lock(self.directory):
            return self._get_pending(confirm_id)

    def pending_payments(self) -> list[Pending]:
        """Pending hot payments still inside the confirm window, oldest first.

        Does not create a state file. A missing file means there is nothing to
        show; writing an unsigned file here would fail closed once a secret
        is configured.
        """
        with exclusive_lock(self.directory):
            if not self.state_path.exists():
                return []
            state = self._state()
            before = set(state["pending"])
            self._expire(state)
            if set(state["pending"]) != before:
                self._write_state(state)
            found = [Pending(**value) for value in state["pending"].values()]
        found.sort(key=lambda item: item.created)
        return found

    def _get_pending(self, confirm_id: str) -> Pending:
        state = self._state()
        self._expire(state)
        self._write_state(state)
        if confirm_id not in state["pending"]:
            raise PolicyError("unknown or expired confirm_id")
        return Pending(**state["pending"][confirm_id])

    def verify_approval(self, pending: Pending, code: str, secret: str | None) -> None:
        with exclusive_lock(self.directory):
            self._verify(pending, code, secret)

    def _verify(self, pending: Pending, code: str, secret: str | None) -> None:
        if not secret:
            raise PolicyError("server has no WALLET_MCP_APPROVAL_SECRET; sending disabled")
        state = self._state()
        if state["failed"] >= self.max_failed:
            raise PolicyError(
                "locked after too many bad approval codes; human must run `wallet-mcp unlock --coin "
                + self.coin
                + "`"
            )
        expected = approval_code(
            secret, pending.coin, pending.network, pending.role, pending.confirm_id,
            pending.address, pending.amount, pending.fee, extra=pending.extra,
        )
        if not _matches(expected, code):
            state["failed"] += 1
            self._write_state(state)
            raise PolicyError("invalid approval code")
        state["failed"] = 0
        self._write_state(state)

    def authorize_and_reserve(self, confirm_id: str, code: str, secret: str | None) -> Pending:
        """Check the code and the limits, then record the spend before broadcast.

        The lock covers the whole check-and-record so two sends cannot both
        observe the same residual daily budget.
        """
        with exclusive_lock(self.directory):
            pending = self._get_pending(confirm_id)
            if pending.coin != self.coin:
                raise PolicyError("confirm_id is for a different coin")
            self._verify(pending, code, secret)
            self._check_total(pending.amount, pending.fee)
            self._discard(confirm_id)
            self._append(pending, "")
            return pending

    def reserve_auto(self, confirm_id: str) -> Pending | None:
        """Reserve one hot payment against the autonomous budget, before broadcast.

        Returns the pending payment when it fit and was recorded with
        ``auto`` set. Returns None when it does not fit, leaving the pending
        payment in place for a human approval code. The lock covers the
        check and the append so two payments cannot both pass the same
        residual budget.
        """
        with exclusive_lock(self.directory):
            return self._reserve_auto(confirm_id, parent=None, parent_fee=0)

    def reserve_auto_with_parent(self, confirm_id: str, parent: Policy, parent_fee: int) -> Pending | None:
        """Same as ``reserve_auto``, and reserve the parent coin's gas first.

        The parent directory is locked before this coin so a native spend of
        the parent and a token auto-spend cannot deadlock.
        """
        with exclusive_lock(parent.directory):
            with exclusive_lock(self.directory):
                return self._reserve_auto(confirm_id, parent=parent, parent_fee=parent_fee)

    def _reserve_auto(self, confirm_id: str, parent: Policy | None, parent_fee: int) -> Pending | None:
        pending = self._get_pending(confirm_id)
        if pending.coin != self.coin:
            raise PolicyError("confirm_id is for a different coin")
        if pending.role != "hot":
            raise PolicyError("only the hot wallet can spend; cold is watch-only and cannot sign")
        self._check_total(pending.amount, pending.fee)
        total = pending.amount + pending.fee
        if not self._within_auto(total) or self._secret() is None:
            return None
        if parent is not None:
            parent._check_total(0, parent_fee)
            if parent._secret() is None or not parent._within_auto(parent_fee):
                return None
        self._discard(confirm_id)
        self._append(pending, "", auto=True)
        if parent is not None:
            parent._note_external_unlocked(
                "", parent_fee, f"gas for {self.coin}", confirm_id=pending.confirm_id, auto=True,
            )
        return pending

    def _append(self, pending: Pending, txid: str, *, auto: bool = False) -> None:
        ledger = self._ledger_entries()
        ledger.append(
            {
                "ts": self.now(),
                "txid": txid,
                "address": pending.address,
                "total": pending.amount + pending.fee,
                "memo": pending.memo,
                "coin": self.coin,
                "confirm_id": pending.confirm_id,
                "auto": bool(auto),
            }
        )
        self._write_ledger(ledger)

    def attach_txid(self, pending: Pending, txid: str) -> None:
        with exclusive_lock(self.directory):
            ledger = self._ledger_entries()
            for entry in reversed(ledger):
                if entry.get("confirm_id") == pending.confirm_id:
                    entry["txid"] = txid
                    break
            self._write_ledger(ledger)

    def note_external_fee(self, txid: str, fee: int, memo: str, *, confirm_id: str = "", auto: bool = False) -> None:
        """Record gas paid by a parent chain for a token transfer.

        The token ledger counts the token amount. This entry counts the native
        fee against the parent coin's daily limit. It does not touch a pending
        payment. The row is sealed with the same ledger MAC as a native spend,
        and it is written before broadcast so two token sends cannot both pass
        the parent cap. ``auto`` marks gas that was spent inside the parent's
        autonomous budget; it still counts toward the parent's hard daily limit.
        """
        with exclusive_lock(self.directory):
            self._note_external_unlocked(txid, fee, memo, confirm_id=confirm_id, auto=auto)

    def _note_external_unlocked(self, txid: str, fee: int, memo: str, *, confirm_id: str = "", auto: bool = False) -> None:
        fee = self._bounded(fee, "fee")
        if fee <= 0:
            raise PolicyError("fee must be a non-negative integer")
        try:
            memo = assert_safe_memo(memo)
        except GuardError as exc:
            raise PolicyError(str(exc)) from None
        self._check_total(0, fee)
        ledger = self._ledger_entries()
        ledger.append({
            "ts": self.now(),
            "txid": txid,
            "address": "gas",
            "total": fee,
            "memo": memo,
            "coin": self.coin,
            "confirm_id": confirm_id,
            "auto": bool(auto),
        })
        self._write_ledger(ledger)

    def consume(self, pending: Pending, txid: str) -> None:
        with exclusive_lock(self.directory):
            state = self._state()
            state["pending"].pop(pending.confirm_id, None)
            self._write_state(state)
            self._append(pending, txid)

    def _discard(self, confirm_id: str) -> None:
        state = self._state()
        state["pending"].pop(confirm_id, None)
        self._write_state(state)

    def discard(self, confirm_id: str) -> None:
        with exclusive_lock(self.directory):
            self._discard(confirm_id)

    def unlock(self) -> None:
        with exclusive_lock(self.directory):
            try:
                state = self._state()
            except PolicyError:
                state = {"pending": {}, "failed": 0}
            state["failed"] = 0
            self._write_state(state)
