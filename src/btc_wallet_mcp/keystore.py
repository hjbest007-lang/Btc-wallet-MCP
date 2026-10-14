"""Encrypted-at-rest seed storage: scrypt KDF + AES-256-GCM (pyca/cryptography).

The mnemonic is only ever returned to in-process callers (wallet.py, cli.py);
it is never exposed through an MCP tool or written to logs.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VERSION = 1
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSPHRASE_LEN = 12


class KeystoreError(Exception):
    pass


def _kdf(passphrase: str, salt: bytes, n: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def encrypt_seed(mnemonic: str, passphrase: str, *, n: int = SCRYPT_N) -> dict:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise KeystoreError(f"passphrase must be at least {MIN_PASSPHRASE_LEN} chars")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    aad = f"btc-wallet-mcp:v{VERSION}".encode()
    ct = AESGCM(_kdf(passphrase, salt, n)).encrypt(nonce, mnemonic.encode(), aad)
    return {"v": VERSION, "kdf": "scrypt", "n": n, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}


def decrypt_seed(blob: dict, passphrase: str) -> str:
    if blob.get("v") != VERSION or blob.get("kdf") != "scrypt":
        raise KeystoreError("unsupported keystore format")
    aad = f"btc-wallet-mcp:v{VERSION}".encode()
    try:
        key = _kdf(passphrase, bytes.fromhex(blob["salt"]), int(blob["n"]))
        pt = AESGCM(key).decrypt(bytes.fromhex(blob["nonce"]), bytes.fromhex(blob["ct"]), aad)
    except (InvalidTag, KeyError, ValueError):
        raise KeystoreError("cannot decrypt seed (wrong passphrase or corrupt file)") from None
    return pt.decode()


def write_seed_file(path: Path, mnemonic: str, passphrase: str, *, n: int = SCRYPT_N) -> None:
    path = Path(path)
    if path.exists():
        raise KeystoreError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    data = json.dumps(encrypt_seed(mnemonic, passphrase, n=n)).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def read_seed_file(path: Path, passphrase: str) -> str:
    path = Path(path)
    if not path.exists():
        raise KeystoreError("no wallet found; run `btc-wallet-mcp init` first")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise KeystoreError(f"{path} permissions too open; chmod 600")
    return decrypt_seed(json.loads(path.read_text()), passphrase)
