"""One encrypted BIP39 mnemonic for every hot coin that has no seed of its own.

The file is ``$WALLET_MCP_DATA_DIR/master.enc``, sealed with the same scrypt
and AES-GCM keystore as a per-coin seed. Its AAD is ``master:hot``, so a copy
of the file cannot be opened as some other coin's seed.

A per-coin ``hot/seed.enc`` wins. Decrypting the wrong file, or decrypting
this file with the wrong passphrase, fails closed: it does not fall through
to the other seed. Cold keys are not derived from this mnemonic.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

import bdkpython as bdk

from btc_wallet_mcp.keystore import SCRYPT_N, KeystoreError, read_seed_file, write_seed_file

from .coins import COINS, TOKEN_PARENTS
from .config import ConfigError

MASTER_CONTEXT = "master:hot"
MASTER_NAME = "master.enc"


class MasterError(Exception):
    pass


@dataclass(frozen=True)
class HotSecret:
    value: str = field(repr=False)
    source: str
    owner: str


def master_path(cfg) -> Path:
    return Path(cfg.data_dir) / MASTER_NAME


def generate_mnemonic(words: int = 24) -> str:
    if words not in (12, 24):
        raise MasterError("--words must be 12 or 24")
    count = bdk.WordCount.WORDS12 if words == 12 else bdk.WordCount.WORDS24
    phrase = str(bdk.Mnemonic(count))
    if len(phrase.split()) != words:
        raise MasterError("mnemonic generation failed")
    return phrase


def confirm_backup(mnemonic: str, reader) -> None:
    """Ask for 3 randomly chosen words. A mismatch does not write the seed."""
    words = mnemonic.split()
    if len(words) not in (12, 24):
        raise MasterError("mnemonic must be 12 or 24 words")
    picks = sorted(secrets.SystemRandom().sample(range(len(words)), 3))
    for index in picks:
        typed = reader(f"Word {index + 1}: ")
        if not isinstance(typed, str) or typed.strip().lower() != words[index]:
            raise MasterError(
                "backup check failed; the master seed was not saved. "
                "Run init again to generate a new mnemonic."
            )


def write_master(cfg, passphrase: str, mnemonic: str, *, n: int = SCRYPT_N) -> Path:
    path = master_path(cfg)
    write_seed_file(path, mnemonic, passphrase, n=n, context=MASTER_CONTEXT)
    return path


def read_master(path: Path, passphrase: str) -> str:
    return read_seed_file(
        path, passphrase,
        missing_message="no master seed; run `wallet-mcp init --all --role hot`",
        context=MASTER_CONTEXT,
    )


def _owner(coin_id: str) -> str:
    return TOKEN_PARENTS.get(coin_id, coin_id)


def enabled_coin_seeds(cfg) -> list[str]:
    """Enabled coins that already have their own hot seed file.

    Token coins spend the parent key, so they are not listed on their own.
    """
    found = []
    for coin_id in cfg.enabled_ids():
        if coin_id in TOKEN_PARENTS:
            continue
        if coin_id not in COINS:
            continue
        if (cfg.hot_dir(coin_id) / "seed.enc").exists():
            found.append(coin_id)
    return found


def prepare_init_all(cfg, *, skip_existing: bool) -> list[str]:
    """Return coins whose existing hot seed will be kept. Raise to refuse."""
    if not cfg.enabled_ids():
        raise MasterError("no coins are enabled; run wallet-mcp configure first")
    path = master_path(cfg)
    if path.exists():
        raise MasterError(f"{path} already exists; refusing to overwrite")
    existing = enabled_coin_seeds(cfg)
    if existing and not skip_existing:
        names = ", ".join(existing)
        raise MasterError(
            "refusing: enabled coins already have a hot seed (" + names + "). "
            "That file takes precedence over the master seed. "
            "Re-run with --skip-existing to keep those seeds and derive the other coins "
            "from the new mnemonic."
        )
    return existing


def load_hot_secret(cfg, coin_id: str) -> HotSecret | None:
    """Per-coin seed file first. Otherwise the master mnemonic.

    A decrypt failure raises. It does not skip a bad per-coin file and open
    the master, and it does not treat a bad passphrase as "no wallet".
    """
    if coin_id not in COINS:
        raise ConfigError(f"unknown coin {coin_id}")
    owner = _owner(coin_id)
    coin_path = cfg.hot_dir(owner) / "seed.enc"
    if coin_path.exists():
        path, source, context = coin_path, "coin", f"{owner}:hot"
        missing = f"no hot {owner} wallet; run wallet-mcp init"
    else:
        path = master_path(cfg)
        if not path.exists():
            return None
        source, context = "master", MASTER_CONTEXT
        missing = "no master seed; run `wallet-mcp init --all --role hot`"
    passphrase = cfg.env.get("WALLET_MCP_PASSPHRASE")
    if not passphrase:
        raise ConfigError("WALLET_MCP_PASSPHRASE not set")
    try:
        value = read_seed_file(path, passphrase, missing_message=missing, context=context)
    except KeystoreError as exc:
        raise ConfigError(str(exc)) from None
    return HotSecret(value, source, owner)


def summary_lines(cfg, skipped: list[str]) -> list[str]:
    from .derive import first_address_path

    skipped_set = set(skipped)
    lines = []
    for coin_id in cfg.enabled_ids():
        network = cfg.coins[coin_id].network
        path = first_address_path(coin_id, network)
        if coin_id in TOKEN_PARENTS:
            parent = TOKEN_PARENTS[coin_id]
            lines.append(f"{coin_id}: uses the {parent} key ({path})")
            continue
        if coin_id in skipped_set:
            lines.append(f"{coin_id}: kept existing hot seed (takes precedence)")
            continue
        note = ""
        if coin_id == "avax":
            note = " — same address as eth when both use this mnemonic"
        lines.append(f"{coin_id} ({network}): {path}{note}")
    return lines
