"""Spending policy: limits, rolling 24h ledger, pending payments, human approval codes."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config

DAY = 86_400
MIN_APPROVAL_SECRET_LEN = 16


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


def _atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


class Policy:
    def __init__(self, cfg: Config, now=time.time):
        self.cfg = cfg
        self.now = now
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = cfg.data_dir / "ledger.json"
        self.state_path = cfg.data_dir / "pending.json"

    # --- persistence -------------------------------------------------
    def _ledger(self) -> list[dict]:
        return json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"pending": {}, "failed": 0}

    def spent_last_24h(self) -> int:
        cutoff = self.now() - DAY
        return sum(e["total_sats"] for e in self._ledger() if e["ts"] >= cutoff)

    # --- checks ------------------------------------------------------
    def check_request(self, address: str, amount_sats: int) -> None:
        if not isinstance(amount_sats, int) or isinstance(amount_sats, bool) or amount_sats <= 0:
            raise PolicyError("amount_sats must be a positive integer")
        if self.cfg.allowlist and address not in self.cfg.allowlist:
            raise PolicyError("address not on allowlist")
        if amount_sats > self.cfg.max_sats_per_tx:
            raise PolicyError(f"amount exceeds per-tx limit ({self.cfg.max_sats_per_tx} sats)")

    def check_total(self, amount_sats: int, fee_sats: int) -> None:
        total = amount_sats + fee_sats
        if total > self.cfg.max_sats_per_tx:
            raise PolicyError(f"amount+fee exceeds per-tx limit ({self.cfg.max_sats_per_tx} sats)")
        spent = self.spent_last_24h()
        if spent + total > self.cfg.max_sats_per_day:
            raise PolicyError(
                f"daily limit: {spent} spent in last 24h, limit {self.cfg.max_sats_per_day} sats"
            )

    # --- pending -----------------------------------------------------
    def add_pending(self, address, amount_sats, fee_sats, memo, psbt) -> Pending:
        st = self._state()
        self._expire(st)
        p = Pending("pay_" + secrets.token_hex(8), address, amount_sats, fee_sats,
                    memo, psbt, self.now())
        st["pending"][p.confirm_id] = asdict(p)
        _atomic_write(self.state_path, st)
        return p

    def _expire(self, st) -> None:
        cutoff = self.now() - self.cfg.confirm_ttl_seconds
        st["pending"] = {k: v for k, v in st["pending"].items() if v["created"] >= cutoff}

    def get_pending(self, confirm_id: str) -> Pending:
        st = self._state()
        self._expire(st)
        _atomic_write(self.state_path, st)
        if confirm_id not in st["pending"]:
            raise PolicyError("unknown or expired confirm_id")
        return Pending(**st["pending"][confirm_id])

    def verify_approval(self, p: Pending, code: str, secret: str | None) -> None:
        if not secret:
            raise PolicyError("server has no BTC_WALLET_APPROVAL_SECRET; sending disabled")
        st = self._state()
        if st["failed"] >= self.cfg.max_failed_approvals:
            raise PolicyError("locked after too many bad approval codes; human must run `btc-wallet-mcp unlock`")
        expected = approval_code(secret, p.confirm_id, p.address, p.amount_sats, p.fee_sats)
        if not hmac.compare_digest(expected, (code or "").strip().lower()):
            st["failed"] += 1
            _atomic_write(self.state_path, st)
            raise PolicyError("invalid approval code")
        st["failed"] = 0
        _atomic_write(self.state_path, st)

    def consume(self, p: Pending, txid: str) -> None:
        st = self._state()
        st["pending"].pop(p.confirm_id, None)
        _atomic_write(self.state_path, st)
        led = self._ledger()
        led.append({"ts": self.now(), "txid": txid, "address": p.address,
                    "total_sats": p.amount_sats + p.fee_sats, "memo": p.memo})
        _atomic_write(self.ledger_path, led)

    def discard(self, confirm_id: str) -> None:
        st = self._state()
        st["pending"].pop(confirm_id, None)
        _atomic_write(self.state_path, st)

    def unlock(self) -> None:
        st = self._state()
        st["failed"] = 0
        _atomic_write(self.state_path, st)
