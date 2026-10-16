"""Thin wrapper around BDK (bdkpython). All key handling/signing is done by BDK."""
from __future__ import annotations

import json
import math
import os

import bdkpython as bdk

from .config import Config
from .guard import open_chain
from .keystore import read_seed_file

NETWORKS = {
    "bitcoin": bdk.Network.BITCOIN,
    "testnet": bdk.Network.TESTNET,
    "testnet4": bdk.Network.TESTNET4,
    "signet": bdk.Network.SIGNET,
    "regtest": bdk.Network.REGTEST,
}
STOP_GAP = 20


class WalletError(Exception):
    pass


def new_mnemonic() -> str:
    return str(bdk.Mnemonic(bdk.WordCount.WORDS12))


def descriptors_from_mnemonic(mnemonic: str, network: str):
    net = NETWORKS[network]
    key = bdk.DescriptorSecretKey(net, bdk.Mnemonic.from_string(mnemonic), None)
    ext = bdk.Descriptor.new_bip84(key, bdk.KeychainKind.EXTERNAL, net)
    chg = bdk.Descriptor.new_bip84(key, bdk.KeychainKind.INTERNAL, net)
    return ext, chg


class BtcWallet:
    def __init__(self, cfg: Config, passphrase: str, client=None):
        self.cfg = cfg
        self.net = NETWORKS[cfg.network]
        mnemonic = read_seed_file(cfg.data_dir / "seed.enc", passphrase, context="btc")
        ext, chg = descriptors_from_mnemonic(mnemonic, cfg.network)
        del mnemonic
        db = cfg.data_dir / "wallet.sqlite"  # BDK stores public descriptors/chain data only
        existed = db.exists()
        self.persister = bdk.Persister.new_sqlite(str(db))
        if existed:
            self.w = bdk.Wallet.load(ext, chg, self.persister)
        else:
            self.w = bdk.Wallet(ext, chg, self.net, self.persister)
            self.w.persist(self.persister)
        os.chmod(db, 0o600)
        self.client = client or bdk.EsploraClient(cfg.esplora_url)

    def sync(self) -> None:
        req = self.w.start_full_scan().build()
        upd = self.client.full_scan(req, STOP_GAP, 4)
        self.w.apply_update(upd)
        self.w.persist(self.persister)

    def new_address(self) -> str:
        info = self.w.reveal_next_address(bdk.KeychainKind.EXTERNAL)
        self.w.persist(self.persister)
        return str(info.address)

    def balance(self) -> dict:
        b = self.w.balance()
        return {"confirmed_sats": b.confirmed.to_sat(),
                "trusted_pending_sats": b.trusted_pending.to_sat(),
                "untrusted_pending_sats": b.untrusted_pending.to_sat(),
                "total_sats": b.total.to_sat()}

    def transactions(self, limit: int = 25) -> list[dict]:
        out = []
        for ct in self.w.transactions():
            tx = ct.transaction
            sr = self.w.sent_and_received(tx)
            pos = ct.chain_position
            if pos.is_confirmed():
                height = pos.confirmation_block_time.block_id.height
                ts = pos.confirmation_block_time.confirmation_time
            else:
                height, ts = None, None
            try:
                fee = self.w.calculate_fee(tx).to_sat()
            except Exception:
                fee = None
            out.append({"txid": str(tx.compute_txid()), "sent_sats": sr.sent.to_sat(),
                        "received_sats": sr.received.to_sat(), "fee_sats": fee,
                        "confirmed": pos.is_confirmed(), "height": height, "time": ts})
        out.sort(key=lambda t: (t["confirmed"], t["time"] or 0), reverse=False)
        out.sort(key=lambda t: -(t["time"] or 1e18))
        return out[:limit]

    def parse_address(self, address: str) -> bdk.Address:
        try:
            a = bdk.Address(address, self.net)
        except Exception:
            raise WalletError("invalid address for this network") from None
        if self.w.is_mine(a.script_pubkey()):
            raise WalletError("refusing to pay to own address")
        return a

    def _fee_estimates(self) -> dict:
        try:
            return self.client.get_fee_estimates()
        except Exception:
            # mempool.space answers fee-estimates with HTTP 203, which BDK rejects.
            with open_chain(self.cfg.esplora_url + "/fee-estimates", timeout=15, mainnet=self.cfg.is_mainnet) as response:
                return {int(k): float(v) for k, v in json.load(response).items()}

    def fee_rate(self, target_blocks: int = 6) -> int:
        est = self._fee_estimates()
        rate = est.get(target_blocks) or (min(est.values()) if est else 1.0)
        rate = max(1, math.ceil(rate))
        if rate > self.cfg.max_fee_rate_sat_vb:
            raise WalletError(f"network fee {rate} sat/vB exceeds max {self.cfg.max_fee_rate_sat_vb}")
        return rate

    def build(self, address: str, amount_sats: int, rate: int) -> tuple[str, int]:
        a = self.parse_address(address)
        psbt = (bdk.TxBuilder()
                .add_recipient(a.script_pubkey(), bdk.Amount.from_sat(amount_sats))
                .fee_rate(bdk.FeeRate.from_sat_per_vb(rate))
                .finish(self.w))
        fee = psbt.fee()
        # The builder reserves change addresses; we don't persist until send.
        return psbt.serialize(), fee

    def sign_and_broadcast(self, psbt_b64: str, address: str, amount_sats: int, fee_sats: int) -> str:
        psbt = bdk.Psbt(psbt_b64)
        tx = psbt.extract_tx()
        # Re-verify the PSBT really pays exactly what the human approved.
        spk = bdk.Address(address, self.net).script_pubkey().to_bytes()
        pays = sum(o.value.to_sat() for o in tx.output() if o.script_pubkey.to_bytes() == spk)
        foreign = [o for o in tx.output()
                   if o.script_pubkey.to_bytes() != spk and not self.w.is_mine(o.script_pubkey)]
        if pays != amount_sats or foreign or psbt.fee() != fee_sats:
            raise WalletError("PSBT does not match approved payment")
        if not self.w.sign(psbt) :
            raise WalletError("signing failed")
        final = psbt.extract_tx()
        self.client.broadcast(final)
        self.w.persist(self.persister)
        return str(final.compute_txid())
