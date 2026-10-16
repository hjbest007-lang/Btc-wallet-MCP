"""Spend capability minted only by the payment gate.

Adapters receive this object as an argument of ``execute``. They cannot construct
a valid one: the seal is a private object held by ``gate.py`` and checked by
identity. The gate mints it only after the human approval code and the limits
have both been checked.
"""
from __future__ import annotations


class AuthError(Exception):
    pass


class SpendAuthorization:
    def __init__(
        self,
        *,
        coin: str,
        network: str,
        role: str,
        confirm_id: str,
        address: str,
        amount: int,
        fee: int,
        seal: object,
    ):
        # Imported lazily so adapters never need the gate module to verify a seal,
        # and so this module does not construct seals of its own.
        from .gate import gate_seal

        if seal is not gate_seal:
            raise AuthError("spend authorization is issued only by the payment gate")
        if role != "hot":
            raise AuthError("cold watch-only wallets cannot be authorized to spend")
        self.coin = coin
        self.network = network
        self.role = role
        self.confirm_id = confirm_id
        self.address = address
        self.amount = amount
        self.fee = fee

    def check(self, *, coin: str, network: str, role: str, address: str, amount: int, fee: int) -> None:
        if (
            self.coin != coin
            or self.network != network
            or self.role != role
            or self.address != address
            or self.amount != amount
            or self.fee != fee
            or role != "hot"
        ):
            raise AuthError("spend authorization does not match this payment")
