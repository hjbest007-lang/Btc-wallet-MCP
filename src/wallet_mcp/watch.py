"""Watch-only documents. Private keys are rejected before they touch disk."""
from __future__ import annotations

import json
import re

from btc_wallet_mcp.guard import contains_wif

from .config import ConfigError

_SECRET_KEYS = {"seed", "mnemonic", "private_key", "privatekey", "secret", "xprv"}
_PRV_MARKERS = ("xprv", "tprv", "yprv", "zprv", "vprv")


def assert_watch_only(document) -> None:
    if not isinstance(document, dict):
        raise ConfigError("watch file must be a JSON object")
    _walk(document)


def _walk(value, key: str = "") -> None:
    if key.lower() in _SECRET_KEYS:
        raise ConfigError("watch-only import contains private key material")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _walk(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _walk(child, key)
    elif isinstance(value, str):
        lowered = value.lower()
        for marker in _PRV_MARKERS:
            if marker in lowered:
                raise ConfigError("watch-only import contains a private key")
        if re.fullmatch(r"s[1-9A-HJ-NP-Za-km-z]{15,}", value):
            raise ConfigError("watch-only import contains an XRP seed")
        if re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", value):
            raise ConfigError("watch-only import contains a private key")
        if contains_wif(value):
            raise ConfigError("watch-only import contains a private key")
        words = value.split()
        if len(words) in {12, 15, 18, 21, 24} and all(word.isalpha() for word in words):
            raise ConfigError("watch-only import looks like a seed phrase")


def load_watch_file(path) -> dict:
    document = json.loads(path.read_text())
    assert_watch_only(document)
    return document
