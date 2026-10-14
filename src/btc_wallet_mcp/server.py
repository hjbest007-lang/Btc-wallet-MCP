"""MCP stdio server exposing a minimal Bitcoin wallet."""
from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .service import Service
from .wallet import BtcWallet


def build_service() -> Service:
    cfg = load_config()
    pw = os.environ.get("BTC_WALLET_PASSPHRASE")
    if not pw:
        raise SystemExit("BTC_WALLET_PASSPHRASE not set")
    return Service(cfg, BtcWallet(cfg, pw))


def make_app(svc: Service) -> FastMCP:
    app = FastMCP("btc-wallet")

    @app.tool()
    def get_config() -> dict:
        """Network, limits and whether sending is enabled. No secrets."""
        return svc.get_config()

    @app.tool()
    def get_address() -> dict:
        """Return a fresh native-segwit (BIP84) receive address."""
        return svc.get_address()

    @app.tool()
    def get_balance() -> dict:
        """Sync with Esplora and return the wallet balance in sats."""
        return svc.get_balance()

    @app.tool()
    def list_transactions(limit: int = 25) -> dict:
        """List recent wallet transactions."""
        return svc.list_transactions(limit)

    @app.tool()
    def prepare_payment(address: str, amount_sats: int, memo: str = "") -> dict:
        """Prepare (do NOT send) an on-chain payment. Returns fee, unsigned PSBT and a
        confirm_id. A human must approve it out-of-band before send_payment works."""
        return svc.prepare_payment(address, amount_sats, memo)

    @app.tool()
    def send_payment(confirm_id: str, approval_code: str) -> dict:
        """Sign and broadcast a prepared payment. Requires the approval code that ONLY
        the human can generate for this exact payment. Never guess codes: 5 wrong codes
        lock the wallet."""
        return svc.send_payment(confirm_id, approval_code)

    return app


def main() -> None:
    # Logs go to stderr (stdout is the MCP channel). Never log secrets.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    make_app(build_service()).run("stdio")


if __name__ == "__main__":
    main()
