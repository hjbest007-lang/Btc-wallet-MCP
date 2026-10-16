"""Small file helpers. Secrets are never written here."""
from __future__ import annotations

import json
import os
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
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


def format_amount(coin_id: str, amount: int) -> str:
    from .coins import COINS

    spec = COINS[coin_id]
    if spec.unit == "wei":
        whole = amount / 10**18
        return f"{amount} wei ({whole:.8f} {spec.symbol})"
    if spec.unit == "drop":
        return f"{amount} drops ({amount / 1_000_000:.6f} XRP)"
    if spec.unit == "sat":
        return f"{amount} sats"
    return f"{amount} {spec.unit}s"
