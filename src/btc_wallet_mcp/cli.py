"""Human-side CLI: init, approve, unlock, backup, address. Not exposed to the agent."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from .config import load_config
from .guard import sanitize_terminal
from .keystore import read_seed_file, write_seed_file
from .policy import Policy, approval_code
from .wallet import new_mnemonic


def _require_tty():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit("refusing: this command must be run by a human in an interactive terminal")


def _passphrase(confirm=False) -> str:
    pw = os.environ.get("BTC_WALLET_PASSPHRASE") or getpass.getpass("Seed passphrase: ")
    if confirm and not os.environ.get("BTC_WALLET_PASSPHRASE"):
        if getpass.getpass("Repeat: ") != pw:
            raise SystemExit("passphrases differ")
    return pw


def main(argv=None) -> None:
    ap = argparse.ArgumentParser("btc-wallet-mcp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="generate + encrypt a new seed")
    sub.add_parser("backup", help="show the mnemonic once (TTY only) for paper backup")
    a = sub.add_parser("approve", help="compute approval code for a prepared payment")
    a.add_argument("confirm_id")
    sub.add_parser("unlock", help="reset failed-approval counter")
    sub.add_parser("serve", help="run the MCP stdio server")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.cmd == "serve":
        from .server import main as serve
        return serve()
    if args.cmd == "init":
        _require_tty()
        pw = _passphrase(confirm=True)
        m = new_mnemonic()
        write_seed_file(cfg.data_dir / "seed.enc", m, pw, context="btc")
        print(f"Wallet created for {cfg.network} in {cfg.data_dir}")
        print("Write these 12 words on paper NOW (BIP39, BIP84 m/84'/x'/0'). Shown once:\n")
        print("   " + m + "\n")
        input("Press Enter after writing them down...")
        print("\033[2J\033[H", end="")  # clear screen
        return
    if args.cmd == "backup":
        _require_tty()
        print(read_seed_file(cfg.data_dir / "seed.enc", _passphrase(), context="btc"))
        return
    if args.cmd == "unlock":
        _require_tty()
        Policy(cfg).unlock()
        print("unlocked")
        return
    if args.cmd == "approve":
        _require_tty()
        p = Policy(cfg).get_pending(args.confirm_id)
        print(f"Network : {cfg.network}\nTo      : {sanitize_terminal(p.address)}\nAmount  : {p.amount_sats} sats"
              f"\nFee     : {p.fee_sats} sats\nMemo    : {sanitize_terminal(p.memo)}")
        if input("Approve exactly this payment? type 'yes': ").strip() != "yes":
            raise SystemExit("not approved")
        secret = os.environ.get("BTC_WALLET_APPROVAL_SECRET") or getpass.getpass("Approval secret: ")
        print("Approval code:", approval_code(secret, p.confirm_id, p.address, p.amount_sats, p.fee_sats))


if __name__ == "__main__":
    main()
