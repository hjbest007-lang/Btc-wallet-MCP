"""Spending policy: limits, rolling 24h ledger, pending payments, human approval codes."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from .config import Config
from .guard import (
    MAX_UNIT,
    MIN_APPROVAL_SECRET_LEN,
    GuardError,
    assert_safe_address,
    assert_safe_memo,
    exclusive_lock,
    seal,
    unseal,
)


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(obj, handle)
    os.replace(tmp, path)

DAY = 86_400


class PolicyError(Exception):
    pass


@dataclass
class Pending:
    confirm_id: str
    address: str
    amount_sats: int
    fee_sats: int
    memo: str
    psbt: str
    created: float


def approval_code(secret: str, confirm_id: str, address: str, amount_sats: int, fee_sats: int) -> str:
    """Code a human computes (CLI `approve`) to authorise exactly one payment."""
    if len(secret) < MIN_APPROVAL_SECRET_LEN:
        raise PolicyError("approval secret too short")
    msg = f"{confirm_id}|{address}|{amount_sats}|{fee_sats}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:12]


class Policy:
    def __init__(
        self,
        cfg: Config,
        now: Callable[[], float] = time.time,
        secret_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.now = now
        self.secret_provider = secret_provider
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = cfg.data_dir / "ledger.json"
        self.state_path = cfg.data_dir / "pending.json"

    def _secret(self) -> str | None:
        if self.secret_provider is None:
            return None
        value = self.secret_provider()
        if not value or len(value) < MIN_APPROVAL_SECRET_LEN:
            return None
        return value

    def _domain(self, kind: str) -> str:
        return f"btc|{self.cfg.network}|{kind}"

    def _write_sealed(self, path: Path, domain: str, body: dict[str, Any]) -> None:
        payload = dict(body)
        secret = self._secret()
        if secret:
            payload["mac"] = seal(secret, domain, body)
        atomic_json(path, payload)

    def _ledger_entries(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        raw = json.loads(self.ledger_path.read_text())
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
        raw = json.loads(self.state_path.read_text())
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
        self._write_sealed(
            self.state_path,
            self._domain("state"),
            {"pending": state["pending"], "failed": state["failed"]},
        )

    def spent_last_24h(self) -> int:
        with exclusive_lock(self.cfg.data_dir):
            return self._spent()

    def _spent(self) -> int:
        cutoff = self.now() - DAY
        total = 0
        for entry in self._ledger_entries():
            if not isinstance(entry, dict):
                raise PolicyError("ledger integrity check failed")
            ts = entry.get("ts")
            amount = entry.get("total_sats")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                raise PolicyError("ledger integrity check failed")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or amount > MAX_UNIT:
                raise PolicyError("ledger integrity check failed")
            if ts >= cutoff:
                total += amount
        return total

    def _bounded(self, value: object, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value > MAX_UNIT:
            raise PolicyError(f"{label} must be an integer")
        return value

    def check_request(self, address: str, amount_sats: int) -> str:
        try:
            address = assert_safe_address(address)
        except GuardError as exc:
            raise PolicyError(str(exc)) from None
        amount_sats = self._bounded(amount_sats, "amount")
        if amount_sats <= 0:
            raise PolicyError("amount_sats must be a positive integer")
        if self.cfg.allowlist and address not in self.cfg.allowlist:
            raise PolicyError("address not on allowlist")
        if amount_sats > self.cfg.max_sats_per_tx:
            raise PolicyError(f"amount exceeds per-tx limit ({self.cfg.max_sats_per_tx} sats)")
        return address

    def check_total(self, amount_sats: int, fee_sats: int) -> None:
        with exclusive_lock(self.cfg.data_dir):
            self._check_total(amount_sats, fee_sats)

    def _check_total(self, amount_sats: int, fee_sats: int) -> None:
        amount_sats = self._bounded(amount_sats, "amount")
        fee_sats = self._bounded(fee_sats, "fee")
        if fee_sats < 0:
            raise PolicyError("fee must be a non-negative integer")
        total = amount_sats + fee_sats
        if total > self.cfg.max_sats_per_tx:
            raise PolicyError(f"amount+fee exceeds per-tx limit ({self.cfg.max_sats_per_tx} sats)")
        spent = self._spent()
        if spent + total > self.cfg.max_sats_per_day:
            raise PolicyError(
                f"daily limit: {spent} spent in last 24h, limit {self.cfg.max_sats_per_day} sats"
            )

    def add_pending(self, address: str, amount_sats: int, fee_sats: int, memo: str, psbt: str) -> Pending:
        with exclusive_lock(self.cfg.data_dir):
            try:
                memo = assert_safe_memo(memo)
            except GuardError as exc:
                raise PolicyError(str(exc)) from None
            state = self._state()
            self._expire(state)
            pending = Pending(
                "pay_" + secrets.token_hex(8),
                address,
                amount_sats,
                fee_sats,
                memo,
                psbt,
                self.now(),
            )
            state["pending"][pending.confirm_id] = asdict(pending)
            self._write_state(state)
            return pending

    def _expire(self, state: dict[str, Any]) -> None:
        cutoff = self.now() - self.cfg.confirm_ttl_seconds
        state["pending"] = {key: value for key, value in state["pending"].items() if value["created"] >= cutoff}

    def get_pending(self, confirm_id: str) -> Pending:
        with exclusive_lock(self.cfg.data_dir):
            return self._get_pending(confirm_id)

    def _get_pending(self, confirm_id: str) -> Pending:
        state = self._state()
        self._expire(state)
        self._write_state(state)
        if confirm_id not in state["pending"]:
            raise PolicyError("unknown or expired confirm_id")
        return Pending(**state["pending"][confirm_id])

    def _verify(self, pending: Pending, code: str, secret: str | None) -> None:
        if not secret:
            raise PolicyError("server has no BTC_WALLET_APPROVAL_SECRET; sending disabled")
        state = self._state()
        if state["failed"] >= self.cfg.max_failed_approvals:
            raise PolicyError(
                "locked after too many bad approval codes; human must run `btc-wallet-mcp unlock`"
            )
        expected = approval_code(secret, pending.confirm_id, pending.address, pending.amount_sats, pending.fee_sats)
        from .guard import constant_time_equals

        if not constant_time_equals(expected, code or ""):
            state["failed"] += 1
            self._write_state(state)
            raise PolicyError("invalid approval code")
        state["failed"] = 0
        self._write_state(state)

    def verify_approval(self, pending: Pending, code: str, secret: str | None) -> None:
        with exclusive_lock(self.cfg.data_dir):
            self._verify(pending, code, secret)

    def authorize_and_reserve(self, confirm_id: str, code: str, secret: str | None) -> Pending:
        with exclusive_lock(self.cfg.data_dir):
            pending = self._get_pending(confirm_id)
            self._verify(pending, code, secret)
            self._check_total(pending.amount_sats, pending.fee_sats)
            self._discard(confirm_id)
            self._append(pending, "")
            return pending

    def _append(self, pending: Pending, txid: str) -> None:
        ledger = self._ledger_entries()
        ledger.append(
            {
                "ts": self.now(),
                "txid": txid,
                "address": pending.address,
                "total_sats": pending.amount_sats + pending.fee_sats,
                "memo": pending.memo,
                "confirm_id": pending.confirm_id,
            }
        )
        self._write_ledger(ledger)

    def attach_txid(self, pending: Pending, txid: str) -> None:
        with exclusive_lock(self.cfg.data_dir):
            ledger = self._ledger_entries()
            for entry in reversed(ledger):
                if entry.get("confirm_id") == pending.confirm_id:
                    entry["txid"] = txid
                    break
            self._write_ledger(ledger)

    def consume(self, pending: Pending, txid: str) -> None:
        with exclusive_lock(self.cfg.data_dir):
            state = self._state()
            state["pending"].pop(pending.confirm_id, None)
            self._write_state(state)
            self._append(pending, txid)

    def _discard(self, confirm_id: str) -> None:
        state = self._state()
        state["pending"].pop(confirm_id, None)
        self._write_state(state)

    def discard(self, confirm_id: str) -> None:
        with exclusive_lock(self.cfg.data_dir):
            self._discard(confirm_id)

    def unlock(self) -> None:
        with exclusive_lock(self.cfg.data_dir):
            try:
                state = self._state()
            except PolicyError:
                state = {"pending": {}, "failed": 0}
            state["failed"] = 0
            self._write_state(state)
