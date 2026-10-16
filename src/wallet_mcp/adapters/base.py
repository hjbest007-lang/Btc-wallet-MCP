"""Chain adapter contract.

``prepare`` builds an unsigned payment and must not broadcast. ``execute`` is
implemented once, here, and is the only spend entry. It demands a
``SpendAuthorization`` minted by the payment gate and refuses cold wallets.
Coin adapters implement ``sign`` and are not allowed to replace ``execute``
(tests assert they don't). The broadcaster callable is supplied by the gate
at execute time; adapters are constructed without one.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..auth import AuthError, SpendAuthorization


class AdapterError(Exception):
    pass


@dataclass(frozen=True)
class PreparedTransfer:
    address: str
    amount: int
    fee: int
    payload: str
    fee_rate: int | None = None
    # Bound into the approval HMAC when set (for example ``tag:123``).
    extra: str = ""


class ChainAdapter:
    coin_id: str
    network: str
    role: str
    unit: str
    can_sign: bool
    experimental: bool
    supports_transactions: bool = False

    def balance(self) -> dict:
        raise AdapterError("balance is not available")

    def receive_address(self) -> str:
        raise AdapterError("receive address is not available")

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        raise AdapterError("this wallet cannot prepare a payment")

    def list_transactions(self, limit: int = 25) -> list[dict]:
        raise AdapterError("transaction list is not available")

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        raise AdapterError("this wallet cannot sign")

    def execute(self, prepared: PreparedTransfer, authorization, broadcaster) -> str:
        if not isinstance(authorization, SpendAuthorization):
            raise AdapterError("missing spend authorization from the payment gate")
        try:
            authorization.check(
                coin=self.coin_id,
                network=self.network,
                role=self.role,
                address=prepared.address,
                amount=prepared.amount,
                fee=prepared.fee,
            )
        except AuthError as exc:
            raise AdapterError(str(exc)) from None
        if not self.can_sign or self.role != "hot":
            raise AdapterError("cold watch-only wallet cannot sign")
        return self.sign(prepared, broadcaster)


class UnconfiguredHot(ChainAdapter):
    supports_transactions = False

    def __init__(self, coin_id: str, network: str, unit: str, experimental: bool):
        self.coin_id = coin_id
        self.network = network
        self.unit = unit
        self.role = "hot"
        self.can_sign = False
        self.experimental = experimental

    def _missing(self):
        raise AdapterError(
            f"hot {self.coin_id} wallet is not initialized; "
            f"run `wallet-mcp init --coin {self.coin_id} --role hot`"
        )

    def balance(self) -> dict:
        self._missing()

    def receive_address(self) -> str:
        self._missing()

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        self._missing()

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        raise AdapterError("cold watch-only wallet cannot sign")


class UnconfiguredCold(ChainAdapter):
    def __init__(self, coin_id: str, network: str, unit: str, experimental: bool):
        self.coin_id = coin_id
        self.network = network
        self.unit = unit
        self.role = "cold"
        self.can_sign = False
        self.experimental = experimental

    def _missing(self):
        raise AdapterError(
            f"cold {self.coin_id} watch-only data is not configured; "
            "export an xpub or address from the owner machine and "
            f"run `wallet-mcp import-watch --coin {self.coin_id}`"
        )

    def balance(self) -> dict:
        self._missing()

    def receive_address(self) -> str:
        self._missing()

    def prepare(self, address: str, amount: int, memo: str = "", destination_tag: int | None = None) -> PreparedTransfer:
        raise AdapterError("cold watch-only wallet cannot sign")

    def sign(self, prepared: PreparedTransfer, broadcaster) -> str:
        raise AdapterError("cold watch-only wallet cannot sign")
