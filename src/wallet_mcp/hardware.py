"""Owner-side hardware signing for a large cold payment.

The agent server has no tool for this. ``sign_cold_payment`` refuses agent
mode before it touches a device, and it refuses a device that did not report
a physical confirmation. Ledger and Trezor classes do not sign in software:
without a transport that just collected a button press, they raise.

This module does not broadcast. The hot-wallet gate is not involved and is
not weakened: a hardware signature does not spend the hot key and does not
change hot limits.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from eth_account.messages import encode_typed_data

from .policy import hardware_approval_code

PSBT_MAGIC = b"psbt\xff"


class HardwareError(Exception):
    pass


class DeviceResponse:
    def __init__(self, signature: bytes, user_confirmed: bool):
        self.signature = signature
        self.user_confirmed = bool(user_confirmed)


class ScriptedDevice:
    """Test double. Production devices are LedgerDevice and TrezorDevice.

    ``confirm`` stands in for the button on the device. Tests set it explicitly.
    The CLI never constructs this class.
    """

    def __init__(self, confirm: bool, signature: bytes = b"device-signature"):
        self.confirm = confirm
        self.signature = signature
        self.seen: list[tuple] = []

    def sign_psbt(self, psbt: bytes, summary: dict) -> DeviceResponse:
        self.seen.append(("psbt", bytes(psbt), dict(summary)))
        return DeviceResponse(self.signature if self.confirm else b"", self.confirm)

    def sign_eip712(self, typed: dict, summary: dict) -> DeviceResponse:
        self.seen.append(("eip712", typed, dict(summary)))
        return DeviceResponse(self.signature if self.confirm else b"", self.confirm)


def _confirmed(response) -> DeviceResponse:
    if not isinstance(response, DeviceResponse) or not response.user_confirmed:
        raise HardwareError("physical confirmation missing; refusing to continue")
    if not response.signature:
        raise HardwareError("device returned no signature")
    return response


class _TransportDevice:
    kind = "device"

    def __init__(self, transport=None):
        self.transport = transport

    def _open(self):
        if self.transport is not None:
            return self.transport
        raise HardwareError(
            f"no {self.kind} connected. Confirm the payment on the device itself. "
            "This process will not sign a cold key in software."
        )

    def sign_psbt(self, psbt: bytes, summary: dict) -> DeviceResponse:
        return _confirmed(self._open().sign_psbt(psbt, summary))

    def sign_eip712(self, typed: dict, summary: dict) -> DeviceResponse:
        return _confirmed(self._open().sign_eip712(typed, summary))


class LedgerDevice(_TransportDevice):
    kind = "ledger"


class TrezorDevice(_TransportDevice):
    kind = "trezor"


def device_by_name(name: str):
    if name == "ledger":
        return LedgerDevice()
    if name == "trezor":
        return TrezorDevice()
    raise HardwareError("unknown device; choose ledger or trezor")


def _canonical(document: dict) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def payload_hash(document: dict) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def build_psbt_document(coin: str, network: str, address: str, amount: int, fee: int,
                        change: list[dict] | None = None, psbt: bytes | None = None) -> dict:
    """Unsigned PSBT description. The raw PSBT is what a device would display.

    A placeholder PSBT is rejected. Callers pass real PSBT bytes (magic
    ``psbt\\xff``) produced by a watch-only wallet. This function does not sign.
    """
    if coin not in {"btc", "ltc"}:
        raise HardwareError("PSBT signing is only for bitcoin and litecoin")
    raw = psbt if psbt is not None else PSBT_MAGIC + b"\x00"
    if not raw.startswith(PSBT_MAGIC):
        raise HardwareError("PSBT is missing the psbt magic")
    outputs = [{"address": address, "value": amount, "change": False}]
    for item in change or []:
        outputs.append({"address": item["address"], "value": int(item["value"]), "change": True})
    return {
        "format": "psbt-v0",
        "coin": coin,
        "network": network,
        "fee": fee,
        "outputs": outputs,
        "psbt": base64.b64encode(raw).decode(),
    }


def build_eip712_document(coin: str, network: str, chain_id: int, address: str, amount: int, fee: int) -> dict:
    """EIP-712 payment the device shows. Encoding is checked with eth-account.

    No local key is used. ``encode_typed_data`` only checks the structure.
    """
    if coin not in {"eth", "avax"}:
        raise HardwareError("EIP-712 signing is only for ethereum and avalanche")
    if not isinstance(chain_id, int) or isinstance(chain_id, bool) or chain_id <= 0:
        raise HardwareError("missing chain id")
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Payment": [
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "fee", "type": "uint256"},
                {"name": "coin", "type": "string"},
                {"name": "network", "type": "string"},
            ],
        },
        "primaryType": "Payment",
        "domain": {"name": "wallet-mcp", "version": "1", "chainId": chain_id},
        "message": {
            "to": address,
            "value": amount,
            "fee": fee,
            "coin": coin,
            "network": network,
        },
    }
    encode_typed_data(full_message=typed)
    return {
        "format": "eip712",
        "coin": coin,
        "network": network,
        "chainId": chain_id,
        "address": address,
        "amount": amount,
        "fee": fee,
        "typed": typed,
    }


def payment_summary(document: dict) -> dict:
    kind = document.get("format")
    if kind == "psbt-v0":
        outputs = document.get("outputs") or []
        foreign = [item for item in outputs if not item.get("change")]
        if len(foreign) != 1:
            raise HardwareError("PSBT must have exactly one payment output")
        target = foreign[0]
        psbt = base64.b64decode(document.get("psbt") or "")
        if not psbt.startswith(PSBT_MAGIC):
            raise HardwareError("PSBT is missing the psbt magic")
        return {
            "kind": "psbt",
            "coin": document["coin"],
            "network": document["network"],
            "address": target["address"],
            "amount": int(target["value"]),
            "fee": int(document["fee"]),
            "psbt": psbt,
        }
    if kind == "eip712":
        typed = document.get("typed") or {}
        message = typed.get("message") or {}
        if message.get("to") != document.get("address") or int(message.get("value")) != int(document.get("amount")):
            raise HardwareError("EIP-712 message does not match the payment")
        if int(typed.get("domain", {}).get("chainId", -1)) != int(document["chainId"]):
            raise HardwareError("EIP-712 chain id does not match")
        encode_typed_data(full_message=typed)
        return {
            "kind": "eip712",
            "coin": document["coin"],
            "network": document["network"],
            "address": document["address"],
            "amount": int(document["amount"]),
            "fee": int(document["fee"]),
            "typed": typed,
        }
    raise HardwareError("unsupported hardware payload")


def _matches(expected: str, given: str) -> bool:
    candidate = (given or "").strip().lower()
    if len(candidate) != len(expected):
        return False
    return hmac.compare_digest(expected, candidate)


def sign_cold_payment(
    *,
    mode: str,
    document: dict,
    approval: str,
    secret: str | None,
    device,
    tty_ok: bool,
    broadcaster=None,
) -> dict:
    """Sign one cold payment on a hardware device.

    Order is fixed: refuse the agent, refuse a non-TTY, check the approval
    code, and only then ask the device. A missing physical confirmation never
    reaches ``broadcaster``. This function does not broadcast even after a
    confirmation; ``broadcaster`` is accepted only so tests can prove it stays
    unused.
    """
    if mode == "agent":
        raise HardwareError("refusing: the agent cannot start hardware signing")
    if not tty_ok:
        raise HardwareError("refusing: hardware signing must be run by a human in an interactive terminal")
    if broadcaster is not None:
        # Broadcasting a cold signature is not this server's job. Do not ask the device.
        raise HardwareError("this server does not broadcast a hardware signature")
    summary = payment_summary(document)
    if not secret:
        raise HardwareError("approval secret is required")
    expected = hardware_approval_code(
        secret, summary["coin"], summary["network"], summary["address"],
        summary["amount"], summary["fee"], payload_hash(document),
    )
    if not _matches(expected, approval):
        raise HardwareError("invalid approval code")
    shown = {
        "coin": summary["coin"],
        "network": summary["network"],
        "address": summary["address"],
        "amount": summary["amount"],
        "fee": summary["fee"],
    }
    if summary["kind"] == "psbt":
        response = device.sign_psbt(summary["psbt"], shown)
    else:
        response = device.sign_eip712(summary["typed"], shown)
    confirmed = _confirmed(response)
    return {
        "ok": True,
        "coin": summary["coin"],
        "address": summary["address"],
        "amount": summary["amount"],
        "fee": summary["fee"],
        "signature_hex": confirmed.signature.hex(),
        "broadcast": False,
    }
