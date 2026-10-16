"""The only spend path. Every coin uses it; adapters cannot reach a broadcaster
until ``send_payment`` has checked the human code and the limits again.
"""
from __future__ import annotations

import logging

from .adapters.base import AdapterError, PreparedTransfer
from .auth import SpendAuthorization
from .policy import Policy, PolicyError

log = logging.getLogger("wallet_mcp")
MAX_MEMO = 140

# Identity seal. SpendAuthorization accepts only this exact object.
gate_seal = object()


def _issue(pending) -> SpendAuthorization:
    return SpendAuthorization(
        coin=pending.coin,
        network=pending.network,
        role=pending.role,
        confirm_id=pending.confirm_id,
        address=pending.address,
        amount=pending.amount,
        fee=pending.fee,
        seal=gate_seal,
    )


class PaymentGate:
    def __init__(self, adapters: dict, policies: dict, broadcasters: dict, secret_provider, ttl: dict | None = None):
        """``adapters`` is hot wallets only, keyed by coin id.

        ``broadcasters`` are held here and passed into ``execute`` only after
        approval. Nothing in an adapter's constructor receives them.
        """
        self.adapters = adapters
        self.policies = policies
        self.broadcasters = broadcasters
        self._secret = secret_provider
        self.ttl = ttl or {}

    def _hot(self, coin: str, *, spend: bool):
        if coin not in self.adapters:
            raise PolicyError(f"{coin} is not enabled")
        adapter = self.adapters[coin]
        if adapter.role != "hot":
            raise PolicyError("only the hot wallet can spend; cold is watch-only and cannot sign")
        if spend and not adapter.can_sign:
            raise PolicyError("hot wallet cannot sign; cold keys are never loaded on this server")
        return adapter

    def _policy(self, coin: str) -> Policy:
        if coin not in self.policies:
            raise PolicyError(f"{coin} is not enabled")
        return self.policies[coin]

    def prepare_payment(self, coin: str, address: str, amount: int, memo: str = "",
                        destination_tag: int | None = None) -> dict:
        try:
            adapter = self._hot(coin, spend=False)
            policy = self._policy(coin)
            address = address.strip()
            memo = (memo or "")[:MAX_MEMO]
            policy.check_request(address, amount)
            prepared = adapter.prepare(address, amount, memo, destination_tag=destination_tag)
            if not isinstance(prepared, PreparedTransfer):
                raise AdapterError("adapter prepare did not return a transfer")
            if prepared.address != address or prepared.amount != amount:
                raise AdapterError("adapter prepare did not match the requested payment")
            policy.check_total(prepared.amount, prepared.fee)
            pending = policy.add_pending(
                address, amount, prepared.fee, memo, prepared.payload, extra=prepared.extra
            )
        except (PolicyError, AdapterError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.warning("prepare failed: %s", type(exc).__name__)
            return {"ok": False, "error": f"cannot build transaction: {type(exc).__name__}"}
        log.info("prepared %s %s: %s + fee %s", coin, pending.confirm_id, amount, prepared.fee)
        body = {
            "ok": True,
            "confirm_id": pending.confirm_id,
            "coin": coin,
            "network": policy.network,
            "role": "hot",
            "address": address,
            "amount": amount,
            "fee": prepared.fee,
            "unit": policy.unit,
            "total": amount + prepared.fee,
            "memo": memo,
            "unsigned_payload": prepared.payload,
            "expires_in_seconds": self.ttl.get(coin, 600),
            "next_step": (
                "Ask the human to run `wallet-mcp approve --coin "
                + coin + " " + pending.confirm_id
                + "` and give you the approval code, then call " + coin + "_send_payment."
            ),
        }
        if prepared.fee_rate is not None:
            body["fee_rate"] = prepared.fee_rate
        if prepared.extra:
            body["approval_extra"] = prepared.extra
            if prepared.extra.startswith("tag:"):
                body["destination_tag"] = int(prepared.extra.split(":", 1)[1])
        if coin == "btc":
            body["unsigned_psbt"] = prepared.payload
            body["amount_sats"] = amount
            body["fee_sats"] = prepared.fee
        return body

    def send_payment(self, coin: str, confirm_id: str, approval_code: str) -> dict:
        pending = None
        try:
            adapter = self._hot(coin, spend=True)
            policy = self._policy(coin)
            pending = policy.get_pending(confirm_id)
            if pending.coin != coin:
                raise PolicyError("confirm_id is for a different coin")
            policy.verify_approval(pending, approval_code, self._secret())
            policy.check_total(pending.amount, pending.fee)
            # Single use, even if broadcast fails afterwards.
            policy.discard(pending.confirm_id)
            prepared = PreparedTransfer(
                pending.address, pending.amount, pending.fee, pending.payload, extra=pending.extra
            )
            authorization = _issue(pending)
            broadcaster = self.broadcasters[coin]
            fired = {"n": 0}

            def once(raw):
                if fired["n"]:
                    raise AdapterError("broadcaster already used for this payment")
                fired["n"] += 1
                return broadcaster(raw)

            txid = adapter.execute(prepared, authorization, once)
            policy.consume(pending, txid)
        except (PolicyError, AdapterError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            log.warning("send failed: %s", type(exc).__name__)
            return {"ok": False, "error": f"broadcast failed: {type(exc).__name__}"}
        log.info("sent %s %s txid=%s", coin, confirm_id, txid)
        return {"ok": True, "txid": txid, "coin": coin, "amount": pending.amount, "fee": pending.fee,
                "unit": policy.unit}
