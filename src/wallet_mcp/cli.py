"""Operator CLI. Not an MCP tool, and not reachable by the agent without a TTY.

Cold seeds are created only in owner mode and are written outside the directory
the agent server reads for keys. The agent host imports the public export.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from btc_wallet_mcp.keystore import read_seed_file, write_seed_file

from .coins import COIN_ORDER, COINS
from .config import (
    ConfigError,
    apply_enabled,
    approve_limits,
    get_limit_request,
    load_config,
    propose_limits,
)
from .configure import interactive_select, make_httpd
from .material import new_secret, watch_document
from .policy import Policy, approval_code, limit_approval_code
from .util import atomic_json, format_amount
from .watch import assert_watch_only


def _tty() -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit("refusing: this command must be run by a human in an interactive terminal")


def _passphrase(env, confirm: bool = False) -> str:
    pw = env.get("WALLET_MCP_PASSPHRASE") or getpass.getpass("Seed passphrase: ")
    if confirm and not env.get("WALLET_MCP_PASSPHRASE"):
        if getpass.getpass("Repeat: ") != pw:
            raise SystemExit("passphrases differ")
    if len(pw) < 12:
        raise SystemExit("passphrase must be at least 12 characters")
    return pw


def _split(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _policy(cfg, coin_id: str) -> Policy:
    coin = cfg.coins[coin_id]
    return Policy(
        coin_id, coin.network, coin.spec.unit, cfg.policy_dir(coin_id),
        lambda c=coin: (c.max_per_tx, c.max_per_day, c.allowlist),
        ttl=coin.spec.confirm_ttl_seconds,
    )


def _require_coin(coin_id: str) -> None:
    if coin_id not in COINS:
        raise SystemExit(f"unknown coin {coin_id}; choose from {', '.join(COIN_ORDER)}")


def cmd_configure(cfg, args) -> None:
    if args.web:
        port = args.port
        httpd = make_httpd(cfg.env, port)
        bound = httpd.server_address
        print(f"Configure UI at http://{bound[0]}:{bound[1]}/  (localhost only, Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            return
        return
    selected = set(cfg.enabled_ids())
    if args.enable is not None or args.disable is not None:
        selected |= _split(args.enable)
        selected -= _split(args.disable)
    else:
        _tty()
        chosen = interactive_select(selected, input, print)
        if chosen is None:
            print("not saved")
            return
        selected = chosen
    try:
        warnings = apply_enabled(cfg, selected)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    print("enabled:", ", ".join(sorted(selected)) or "(none)")
    for warning in warnings:
        print("warning:", warning)


def cmd_init(cfg, args) -> None:
    _require_coin(args.coin)
    if args.role == "cold" and cfg.mode == "agent":
        raise SystemExit("refusing: create the cold seed on the owner machine, not the agent host")
    _tty()
    coin = cfg.coins[args.coin]
    passphrase = _passphrase(cfg.env, confirm=True)
    secret = new_secret(args.coin)
    if args.role == "hot":
        path = cfg.hot_dir(args.coin) / "seed.enc"
        write_seed_file(path, secret, passphrase)
        print(f"Hot {coin.spec.name} wallet created for {coin.network} in {path.parent}")
    else:
        path = cfg.coin_root(args.coin) / "owner-cold" / "seed.enc"
        write_seed_file(path, secret, passphrase)
        document = watch_document(args.coin, coin.network, secret)
        export_path = cfg.coin_root(args.coin) / "cold-export.json"
        atomic_json(export_path, document)
        print(f"Cold {coin.spec.name} seed stored only at {path}")
        print(f"Public watch file (safe to copy to the agent host): {export_path}")
        print("Do not copy owner-cold/ or seed.enc to the machine that runs the agent.")
    label = "XRP seed" if args.coin == "xrp" else "12 words"
    print(f"\nWrite this {label} on paper NOW. Shown once:\n")
    print("   " + secret + "\n")
    input("Press Enter after writing it down...")
    print("\033[2J\033[H", end="")


def cmd_import_watch(cfg, args) -> None:
    _require_coin(args.coin)
    if cfg.mode == "agent" and (cfg.cold_dir(args.coin) / "seed.enc").exists():
        raise SystemExit("refusing: cold directory contains a seed")
    with open(args.file) as handle:
        document = json.loads(handle.read())
    try:
        assert_watch_only(document)
        # Re-run the coin-specific constructor via watch_document's checks where possible.
        _validate_import(args.coin, cfg.coins[args.coin].network, document)
    except (ConfigError, Exception) as exc:
        if isinstance(exc, ConfigError):
            raise SystemExit(str(exc)) from None
        raise SystemExit("watch file is not valid for this coin and network") from None
    path = cfg.cold_dir(args.coin) / "watch.json"
    atomic_json(path, document)
    print(f"Imported watch-only {args.coin} data to {path}")


def _validate_import(coin_id: str, network: str, document: dict) -> None:
    if coin_id == "btc":
        if document.get("kind") != "bdk-descriptor":
            raise ConfigError("btc watch file must have kind bdk-descriptor")
        import bdkpython as bdk
        from btc_wallet_mcp.wallet import NETWORKS
        bdk.Descriptor(document["external"], NETWORKS[network])
        bdk.Descriptor(document["internal"], NETWORKS[network])
    elif coin_id in {"eth", "avax"}:
        if document.get("kind") != "address":
            raise ConfigError(f"{coin_id} watch file must have kind address")
        from .adapters.evm import checksum
        checksum(document["address"])
    elif coin_id == "xrp":
        if document.get("kind") != "address":
            raise ConfigError("xrp watch file must have kind address")
        from .adapters.xrp import _classic
        _classic(document["address"])
    elif coin_id == "ltc":
        if document.get("kind") != "bip84-xpub":
            raise ConfigError("ltc watch file must have kind bip84-xpub")
        xpub = document.get("xpub") or ""
        if network == "mainnet" and not xpub.startswith("xpub"):
            raise ConfigError("mainnet litecoin watch key must be an xpub")
        if network != "mainnet" and not xpub.startswith("tpub"):
            raise ConfigError("testnet litecoin watch key must be a tpub")
        from embit import bip32
        key = bip32.HDKey.from_base58(xpub)
        if key.is_private:
            raise ConfigError("watch-only import contains a private key")


def cmd_approve(cfg, args) -> None:
    _tty()
    _require_coin(args.coin)
    pending = _policy(cfg, args.coin).get_pending(args.confirm_id)
    print(
        f"Coin    : {pending.coin}\nNetwork : {pending.network}\nRole    : {pending.role}\n"
        f"To      : {pending.address}\nAmount  : {format_amount(pending.coin, pending.amount)}\n"
        f"Fee     : {format_amount(pending.coin, pending.fee)}\nMemo    : {pending.memo}"
    )
    if input("Approve exactly this hot-wallet payment? type 'yes': ").strip() != "yes":
        raise SystemExit("not approved")
    secret = cfg.env.get("WALLET_MCP_APPROVAL_SECRET") or getpass.getpass("Approval secret: ")
    print("Approval code:", approval_code(
        secret, pending.coin, pending.network, pending.role, pending.confirm_id,
        pending.address, pending.amount, pending.fee,
    ))


def cmd_approve_limit(cfg, args) -> None:
    _tty()
    try:
        request = get_limit_request(cfg, args.request_id)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    coin = cfg.coins[request.coin]
    print(
        f"Coin : {request.coin}\n"
        f"From : {coin.max_per_tx} / {coin.max_per_day} {coin.spec.unit}\n"
        f"To   : {request.max_per_tx} / {request.max_per_day} {coin.spec.unit}"
    )
    if input("Approve exactly this limit change? type 'yes': ").strip() != "yes":
        raise SystemExit("not approved")
    secret = cfg.env.get("WALLET_MCP_APPROVAL_SECRET") or getpass.getpass("Approval secret: ")
    code = limit_approval_code(secret, request.request_id, request.coin, request.max_per_tx, request.max_per_day)
    try:
        approve_limits(cfg, request.request_id, code, secret)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    print("limits updated")


def cmd_request_limit(cfg, args) -> None:
    _require_coin(args.coin)
    try:
        status, request = propose_limits(cfg, args.coin, args.max_per_tx, args.max_per_day)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    if status == "pending":
        print(f"pending {request.request_id}: run `wallet-mcp approve-limit {request.request_id}`")
    else:
        print(status)


def cmd_unlock(cfg, args) -> None:
    _tty()
    _require_coin(args.coin)
    _policy(cfg, args.coin).unlock()
    print("unlocked")


def cmd_backup(cfg, args) -> None:
    _require_coin(args.coin)
    if args.role == "cold" and cfg.mode == "agent":
        raise SystemExit("refusing: cold seeds are not kept on the agent host")
    _tty()
    if args.role == "hot":
        path = cfg.hot_dir(args.coin) / "seed.enc"
    else:
        path = cfg.coin_root(args.coin) / "owner-cold" / "seed.enc"
    print(read_seed_file(path, _passphrase(cfg.env)))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser("wallet-mcp")
    sub = parser.add_subparsers(dest="cmd", required=True)

    configure = sub.add_parser("configure", help="tick coins on or off")
    configure.add_argument("--enable", help="comma-separated coins to enable")
    configure.add_argument("--disable", help="comma-separated coins to disable")
    configure.add_argument("--web", action="store_true", help="localhost checkbox page")
    configure.add_argument("--port", type=int, default=8765)

    init = sub.add_parser("init", help="create a hot key, or a cold seed on the owner machine")
    init.add_argument("--coin", required=True)
    init.add_argument("--role", choices=("hot", "cold"), required=True)

    watch = sub.add_parser("import-watch", help="import a public xpub or address for the cold wallet")
    watch.add_argument("--coin", required=True)
    watch.add_argument("--file", required=True)

    approve = sub.add_parser("approve", help="compute the approval code for one prepared payment")
    approve.add_argument("--coin", required=True)
    approve.add_argument("confirm_id")

    limit = sub.add_parser("request-limit", help="lower a limit now, or queue an increase")
    limit.add_argument("--coin", required=True)
    limit.add_argument("--max-per-tx", required=True, type=int)
    limit.add_argument("--max-per-day", required=True, type=int)

    approve_limit = sub.add_parser("approve-limit", help="approve a queued limit increase (TTY)")
    approve_limit.add_argument("request_id")

    unlock = sub.add_parser("unlock", help="reset the failed-approval counter")
    unlock.add_argument("--coin", required=True)

    backup = sub.add_parser("backup", help="show the hot or cold secret (TTY only)")
    backup.add_argument("--coin", required=True)
    backup.add_argument("--role", choices=("hot", "cold"), default="hot")

    sub.add_parser("serve", help="run the MCP stdio server")

    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None

    if args.cmd == "serve":
        from .server import main as serve
        return serve()
    if args.cmd == "configure":
        return cmd_configure(cfg, args)
    if args.cmd == "init":
        return cmd_init(cfg, args)
    if args.cmd == "import-watch":
        return cmd_import_watch(cfg, args)
    if args.cmd == "approve":
        return cmd_approve(cfg, args)
    if args.cmd == "request-limit":
        return cmd_request_limit(cfg, args)
    if args.cmd == "approve-limit":
        return cmd_approve_limit(cfg, args)
    if args.cmd == "unlock":
        return cmd_unlock(cfg, args)
    if args.cmd == "backup":
        return cmd_backup(cfg, args)


if __name__ == "__main__":
    main()
