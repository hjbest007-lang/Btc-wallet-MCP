"""Encrypted-at-rest seed storage: scrypt KDF + AES-256-GCM (pyca/cryptography).

The mnemonic is only ever returned to in-process callers (wallet.py, cli.py);
it is never exposed through an MCP tool or written to logs.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VERSION = 1
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
MIN_N = 2**10
MAX_N = 2**20
MIN_PASSPHRASE_LEN = 12
_CONTEXT = re.compile(r"[a-z0-9:_-]{1,64}")
_LEGACY_AAD = f"btc-wallet-mcp:v{VERSION}".encode()


class KeystoreError(Exception):
    pass


def _check_n(n: int) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < MIN_N or n > MAX_N or n & (n - 1):
        raise KeystoreError("unsupported scrypt parameters")
    return n


def _aad(context: str | None) -> bytes:
    if not context:
        return _LEGACY_AAD
    if not _CONTEXT.fullmatch(context):
        raise KeystoreError("invalid key context")
    return f"btc-wallet-mcp:v{VERSION}:{context}".encode()


def _kdf(passphrase: str, salt: bytes, n: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=_check_n(n), r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def encrypt_seed(mnemonic: str, passphrase: str, *, n: int = SCRYPT_N, context: str | None = None) -> dict:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise KeystoreError(f"passphrase must be at least {MIN_PASSPHRASE_LEN} chars")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_kdf(passphrase, salt, n)).encrypt(nonce, mnemonic.encode(), _aad(context))
    blob = {"v": VERSION, "kdf": "scrypt", "n": n, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}
    if context:
        blob["context"] = context
    return blob


def decrypt_seed(blob: dict, passphrase: str, *, context: str | None = None) -> str:
    if blob.get("v") != VERSION or blob.get("kdf") != "scrypt":
        raise KeystoreError("unsupported keystore format")
    # Files written before context binding still open. A file sealed for another
    # coin or role does not, because its AAD is not the legacy tag.
    aads = []
    if context:
        aads.append(_aad(context))
    if not context or not blob.get("context"):
        aads.append(_LEGACY_AAD)
    try:
        key = _kdf(passphrase, bytes.fromhex(blob["salt"]), int(blob["n"]))
        nonce = bytes.fromhex(blob["nonce"])
        ct = bytes.fromhex(blob["ct"])
    except (KeyError, ValueError):
        raise KeystoreError("cannot decrypt seed (wrong passphrase or corrupt file)") from None
    for aad in aads:
        try:
            return AESGCM(key).decrypt(nonce, ct, aad).decode()
        except InvalidTag:
            continue
    raise KeystoreError("cannot decrypt seed (wrong passphrase or corrupt file)")


def write_seed_file(
    path: Path, mnemonic: str, passphrase: str, *, n: int = SCRYPT_N, context: str | None = None
) -> None:
    path = Path(path)
    if path.exists():
        raise KeystoreError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    data = json.dumps(encrypt_seed(mnemonic, passphrase, n=n, context=context)).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def read_seed_file(
    path: Path, passphrase: str, *, missing_message: str | None = None, context: str | None = None
) -> str:
    path = Path(path)
    if not path.exists():
        raise KeystoreError(missing_message or "no wallet found; run `btc-wallet-mcp init` first")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise KeystoreError(f"{path} permissions too open; chmod 600")
    return decrypt_seed(json.loads(path.read_text()), passphrase, context=context)
