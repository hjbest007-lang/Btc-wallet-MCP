"""Opt-in: BTC_WALLET_INTEGRATION=1 pytest -m integration. Needs internet, no funds."""
import os
import pytest
from btc_wallet_mcp.config import load_config
from btc_wallet_mcp.keystore import write_seed_file
from btc_wallet_mcp.service import Service
from btc_wallet_mcp.wallet import BtcWallet, new_mnemonic

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.environ.get("BTC_WALLET_INTEGRATION") != "1", reason="opt-in")]


def test_signet_empty_wallet_end_to_end(tmp_path):
    cfg = load_config({"BTC_WALLET_DATA_DIR": str(tmp_path)})
    write_seed_file(cfg.data_dir / "seed.enc", new_mnemonic(), "integration-pass", n=2**10)
    s = Service(cfg, BtcWallet(cfg, "integration-pass"), env={"BTC_WALLET_APPROVAL_SECRET": "x" * 20})
    assert s.get_address()["address"].startswith("tb1q")
    assert s.get_balance()["total_sats"] == 0
    assert s.list_transactions()["transactions"] == []
    assert 1 <= s.wallet.fee_rate() <= cfg.max_fee_rate_sat_vb
    r = s.prepare_payment("tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl", 1000, "test")
    assert r["ok"] is False and "cannot build" in r["error"]  # no funds -> refused
