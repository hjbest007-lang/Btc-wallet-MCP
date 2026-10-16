"""Append-only log of prepare, approve, and send attempts.

The log stores ids, addresses, and amounts. It never stores the approval
secret, an approval code, a seed, or a passphrase. A webhook, when configured,
fires only after a successful prepare (an approval is now required). A webhook
failure does not change the payment result.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("wallet_mcp")

_FORBIDDEN = {
    "secret", "approval_secret", "approval_code", "approval", "code",
    "seed", "mnemonic", "passphrase", "private_key", "signature", "signature_hex",
}
_PUBLIC = ("coin", "network", "role", "confirm_id", "address", "amount", "fee", "unit", "ok", "error", "txid")


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, event: str, **fields) -> None:
        safe = {key: fields[key] for key in _PUBLIC if key in fields and key not in _FORBIDDEN}
        safe["event"] = event
        safe["ts"] = time.time()
        line = json.dumps(safe, sort_keys=True, default=str) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        if os.name == "posix":
            os.chmod(self.path, 0o600)


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return True
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost"}:
        return True
    return False


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read()


class WebhookNotifier:
    def __init__(self, url: str | None, poster=None):
        self.url = (url or "").strip()
        self.poster = poster or _post

    def approval_requested(self, body: dict) -> str | None:
        """Return an error string, or None. Never raises into the payment path."""
        if not self.url:
            return None
        if not _allowed(self.url):
            log.warning("approval webhook refused: url scheme or host is not allowed")
            return "webhook url refused"
        payload = {
            "event": "approval_requested",
            "coin": body.get("coin"),
            "network": body.get("network"),
            "confirm_id": body.get("confirm_id"),
            "address": body.get("address"),
            "amount": body.get("amount"),
            "fee": body.get("fee"),
            "unit": body.get("unit"),
        }
        try:
            self.poster(self.url, payload)
        except Exception as exc:
            log.warning("approval webhook failed: %s", type(exc).__name__)
            return f"webhook failed: {type(exc).__name__}"
        return None
