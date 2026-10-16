"""Shared guards: approval-screen text, chain URLs, file locks, sealed JSON.

Chain fetches go through ``open_chain`` only. The URL is checked before the
socket opens, and redirects are refused so a public endpoint cannot bounce the
process at a link-local or ``file:`` target.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import unicodedata
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_MEMO = 140
MAX_ADDRESS_LEN = 256
MAX_UNIT = 10**30
MIN_APPROVAL_SECRET_LEN = 16

_WIF = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])[5KL9c][1-9A-HJ-NP-Za-km-z]{50,51}(?![1-9A-HJ-NP-Za-km-z])"
)
_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.google.com",
}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class GuardError(ValueError):
    """Rejected input. The message is safe to show to the operator."""


def constant_time_equals(expected: str, given: str) -> bool:
    """Compare approval codes without raising on length or non-ASCII input."""
    candidate = (given or "").strip().lower()
    if len(candidate) != len(expected):
        return False
    try:
        return hmac.compare_digest(expected, candidate)
    except (TypeError, ValueError):
        return False


def assert_safe_address(address: str) -> str:
    """Addresses for every supported coin are ASCII. Reject the rest.

    Newlines, ANSI, and bidi controls can rewrite the approval screen so the
    human confirms a different destination than the one that will be signed.
    """
    if not isinstance(address, str):
        raise GuardError("invalid address")
    cleaned = address.strip()
    if not cleaned or len(cleaned) > MAX_ADDRESS_LEN:
        raise GuardError("invalid address")
    if not cleaned.isascii() or any(ord(ch) < 33 or ord(ch) == 127 for ch in cleaned):
        raise GuardError("invalid address")
    return cleaned


def assert_safe_memo(memo: str | None) -> str:
    if memo is None:
        return ""
    if not isinstance(memo, str):
        raise GuardError("invalid memo")
    if len(memo) > MAX_MEMO:
        raise GuardError(f"memo is longer than {MAX_MEMO} characters")
    for char in memo:
        if ord(char) < 32 or ord(char) == 127 or unicodedata.category(char) == "Cf":
            raise GuardError("memo contains unsupported characters")
    return memo


def sanitize_terminal(text: str) -> str:
    """Last line of defence for values already stored before this check existed."""
    parts: list[str] = []
    for char in text:
        if char == "\n":
            parts.append(" ")
        elif ord(char) < 32 or ord(char) == 127 or unicodedata.category(char) == "Cf":
            parts.append("?")
        elif not char.isprintable():
            parts.append("?")
        else:
            parts.append(char)
    return "".join(parts)


def contains_wif(value: str) -> bool:
    return _WIF.search(value) is not None


def validate_chain_url(url: str, *, mainnet: bool = False) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = parsed.hostname
    if parsed.scheme not in {"https", "http"} or not host:
        raise GuardError("chain URL must be https, or http on localhost")
    if parsed.username is not None or parsed.password is not None:
        raise GuardError("chain URL must not include credentials")
    if mainnet and parsed.scheme != "https":
        raise GuardError("mainnet chain URL must use https")
    if parsed.scheme == "http" and host.lower() not in _LOOPBACK:
        raise GuardError("http chain URLs are only allowed for localhost")
    lowered = host.lower().rstrip(".")
    if lowered in _METADATA_HOSTS or lowered.endswith(".internal"):
        raise GuardError("chain URL host is blocked")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_link_local or ip.is_multicast or ip.is_unspecified):
        raise GuardError("chain URL host is blocked")
    return raw.rstrip("/")


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise GuardError("redirects are disabled for chain URLs")


def open_chain(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
    mainnet: bool = False,
) -> Any:
    clean = validate_chain_url(url, mainnet=mainnet)
    request = urllib.request.Request(clean, data=data, headers=headers or {})
    opener = urllib.request.build_opener(_DenyRedirect)
    return opener.open(request, timeout=timeout)


@contextmanager
def exclusive_lock(directory: Path) -> Iterator[None]:
    """Serialize spend accounting across threads and processes."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "posix":
        yield
        return
    lock_path = directory / ".lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)  # noqa: S103 - owner-only lock file
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def seal(secret: str, domain: str, body: Any) -> str:
    if len(secret) < MIN_APPROVAL_SECRET_LEN:
        raise GuardError("approval secret too short")
    message = (domain + "|" + canonical(body)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def unseal(secret: str, domain: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise GuardError("integrity check failed")
    body = dict(data)
    mac = body.pop("mac", None)
    if not isinstance(mac, str):
        raise GuardError("integrity check failed")
    if not constant_time_equals(seal(secret, domain, body), mac):
        raise GuardError("integrity check failed")
    return body
