"""Small file helpers. Secrets are never written here."""
from __future__ import annotations

import json
import os
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        # 0700 is owner-only. Semgrep's file-permission rule treats it as too open
        # and recommends 0644, which would make a secret directory world-readable.
        for directory in [*missing, path]:
            os.chmod(directory, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    return path


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def atomic_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj))


def _fixed(amount: int, scale: int, places: int) -> str:
    sign = "-" if amount < 0 else ""
    whole, frac = divmod(abs(amount), scale)
    return f"{sign}{whole}.{frac:0{places}d}"


def format_amount(coin_id: str, amount: int) -> str:
    from .coins import COINS

    spec = COINS[coin_id]
    if spec.unit == "wei":
        return f"{amount} wei ({_fixed(amount, 10**18, 18)} {spec.symbol})"
    if spec.unit == "drop":
        return f"{amount} drops ({_fixed(amount, 1_000_000, 6)} XRP)"
    if spec.unit == "sat":
        return f"{amount} sats"
    if spec.unit == "token":
        return f"{amount} base units ({amount / 1_000_000:.6f} {spec.symbol})"
    if spec.unit == "lamport":
        return f"{amount} lamports ({amount / 1_000_000_000:.9f} SOL)"
    return f"{amount} {spec.unit}s"
