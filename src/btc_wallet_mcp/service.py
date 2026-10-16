"""Tool logic, independent of MCP transport (easy to unit-test)."""
from __future__ import annotations

import logging
import os

from .config import Config
from .guard import GuardError, assert_safe_memo
from .policy import Policy, PolicyError
from .wallet import WalletError

log = logging.getLogger("btc_wallet_mcp")


class Service:
    def __init__(self, cfg: Config, wallet, policy: Policy | None = None, env=None):
        self.cfg, self.wallet = cfg, wallet
        self.policy = policy or Policy(cfg)
        self._env = os.environ if env is None else env
        self.policy.secret_provider = self._secret

    def _secret(self):
        # Read at call time; never returned or logged.
        return self._env.get("BTC_WALLET_APPROVAL_SECRET")

    def get_config(self) -> dict:
        c = self.cfg
        return {"network": c.network, "esplora_url": c.esplora_url,
                "max_sats_per_tx": c.max_sats_per_tx, "max_sats_per_day": c.max_sats_per_day,
                "max_fee_rate_sat_vb": c.max_fee_rate_sat_vb,
                "allowlist_enabled": bool(c.allowlist),
                "spent_last_24h_sats": self.policy.spent_last_24h(),
                "sending_enabled": bool(self._secret())}

    def get_address(self) -> dict:
        return {"address": self.wallet.new_address(), "network": self.cfg.network}

    def get_balance(self) -> dict:
        self.wallet.sync()
        return {"network": self.cfg.network, **self.wallet.balance()}

    def list_transactions(self, limit: int = 25) -> dict:
        self.wallet.sync()
        return {"transactions": self.wallet.transactions(max(1, min(int(limit), 100)))}

    def prepare_payment(self, address: str, amount_sats: int, memo: str = "") -> dict:
        try:
            memo = assert_safe_memo(memo or "")
            address = self.policy.check_request(address, amount_sats)
            self.wallet.sync()
            rate = self.wallet.fee_rate()
            psbt, fee = self.wallet.build(address, amount_sats, rate)
            self.policy.check_total(amount_sats, fee)
            p = self.policy.add_pending(address, amount_sats, fee, memo, psbt)
        except (PolicyError, WalletError, GuardError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # BDK errors, e.g. insufficient funds
            log.warning("prepare failed: %s", type(e).__name__)
            return {"ok": False, "error": f"cannot build transaction: {type(e).__name__}"}
        log.info("prepared %s: %d sats + %d fee", p.confirm_id, amount_sats, fee)
        return {"ok": True, "confirm_id": p.confirm_id, "network": self.cfg.network,
                "address": address, "amount_sats": amount_sats, "fee_sats": fee,
                "fee_rate_sat_vb": rate, "total_sats": amount_sats + fee, "memo": memo,
                "unsigned_psbt": psbt, "expires_in_seconds": self.cfg.confirm_ttl_seconds,
                "next_step": ("Ask the human to run `btc-wallet-mcp approve " + p.confirm_id +
                              "` and give you the approval code, then call send_payment.")}

    def send_payment(self, confirm_id: str, approval_code: str) -> dict:
        try:
            p = self.policy.authorize_and_reserve(confirm_id, approval_code, self._secret())
            txid = self.wallet.sign_and_broadcast(p.psbt, p.address, p.amount_sats, p.fee_sats)
            try:
                self.policy.attach_txid(p, txid)
            except PolicyError:
                log.warning("ledger update failed after broadcast")
        except (PolicyError, WalletError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.warning("send failed: %s", type(e).__name__)
            return {"ok": False, "error": f"broadcast failed: {type(e).__name__}"}
        log.info("sent %s txid=%s", confirm_id, txid)
        return {"ok": True, "txid": txid, "amount_sats": p.amount_sats, "fee_sats": p.fee_sats}
