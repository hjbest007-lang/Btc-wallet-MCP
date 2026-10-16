"""MCP stdio server. Tools are registered only for coins enabled in the config."""
from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .coins import COINS
from .config import ConfigError, load_config
from .registry import Runtime, build_runtime, tool_names


def make_app(runtime: Runtime) -> FastMCP:
    app = FastMCP("wallet")

    @app.tool()
    def list_coins() -> dict:
        """Coins known to this server, which are enabled, and whether each adapter is tested or experimental."""
        return runtime.list_coins()

    @app.tool()
    def get_config() -> dict:
        """Networks, limits and whether sending is enabled. No secrets, seeds or approval codes."""
        return runtime.get_config()

    for coin_id in runtime.enabled():
        _register_coin(app, runtime, coin_id)
    return app


def _register_coin(app: FastMCP, runtime: Runtime, coin_id: str) -> None:
    spec = COINS[coin_id]
    unit = spec.unit

    def get_coin_config() -> dict:
        body = runtime.get_config()["coins"][coin_id]
        return {"ok": True, "coin": coin_id, **body}

    get_coin_config.__doc__ = f"{spec.name} network, limits and whether the hot wallet can send. No secrets."
    app.tool(name=f"{coin_id}_get_config")(get_coin_config)

    def hot_balance() -> dict:
        return runtime.hot_balance(coin_id)

    hot_balance.__doc__ = f"Hot {spec.name} balance in {unit}. Syncs a public chain source. Does not spend."
    app.tool(name=f"{coin_id}_hot_get_balance")(hot_balance)

    def hot_address() -> dict:
        return runtime.hot_address(coin_id)

    hot_address.__doc__ = (
        f"Fresh hot-wallet receive address for {spec.name}. "
        "Top up this address from the owner's cold wallet; the server cannot sign the cold wallet."
    )
    app.tool(name=f"{coin_id}_hot_get_address")(hot_address)

    def cold_balance() -> dict:
        return runtime.cold_balance(coin_id)

    cold_balance.__doc__ = f"Watch-only cold {spec.name} balance in {unit}. Cannot spend."
    app.tool(name=f"{coin_id}_cold_get_balance")(cold_balance)

    def cold_address() -> dict:
        return runtime.cold_address(coin_id)

    cold_address.__doc__ = f"Watch-only cold {spec.name} receive address. The server has no cold private key."
    app.tool(name=f"{coin_id}_cold_get_address")(cold_address)

    if coin_id == "xrp":
        def prepare_payment(address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> dict:
            return runtime.gate.prepare_payment(
                coin_id, address, amount, memo, destination_tag=destination_tag
            )

        prepare_payment.__doc__ = (
            f"Prepare a hot {spec.name} payment. Amount is an integer in {unit}. "
            "destination_tag is optional, and required for known exchange deposit addresses. "
            "The tag is bound into the approval code. "
            "A payment inside the owner's autonomous budget is broadcast now and needs no approval code. "
            "Anything above that budget returns a confirm_id and is not sent."
        )
    else:
        def prepare_payment(address: str, amount: int, memo: str = "") -> dict:
            return runtime.gate.prepare_payment(coin_id, address, amount, memo)

        prepare_payment.__doc__ = (
            f"Prepare a hot {spec.name} payment. Amount is an integer in {unit}. "
            "A payment inside the owner's autonomous budget is broadcast now and needs no approval code. "
            "Anything above that budget returns a confirm_id and is not sent until a human approves it."
        )
    app.tool(name=f"{coin_id}_prepare_payment")(prepare_payment)

    def send_payment(confirm_id: str, approval_code: str) -> dict:
        return runtime.gate.send_payment(coin_id, confirm_id, approval_code)

    send_payment.__doc__ = (
        f"Sign and broadcast a prepared hot {spec.name} payment that was above the autonomous budget. "
        "Requires the human approval code for this exact payment. Never guess codes: 5 wrong codes lock sending."
    )
    app.tool(name=f"{coin_id}_send_payment")(send_payment)

    if runtime.hot[coin_id].supports_transactions:
        def list_transactions(limit: int = 25) -> dict:
            return runtime.transactions(coin_id, limit)

        list_transactions.__doc__ = f"Recent hot {spec.name} transactions."
        app.tool(name=f"{coin_id}_list_transactions")(list_transactions)


def build_server(env=None) -> FastMCP:
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    return make_app(build_runtime(cfg))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    make_app(build_runtime(load_config())).run("stdio")
