"""Real BDK wallet, offline (fake Esplora client)."""
import pytest
from btc_wallet_mcp.config import load_config
from btc_wallet_mcp.keystore import write_seed_file
from btc_wallet_mcp.wallet import BtcWallet, WalletError, new_mnemonic

M = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
PW = "correct horse battery"


class NoNet:
    def get_fee_estimates(self): return {6: 1.3, 1: 9.0}
    def full_scan(self, *a): raise AssertionError("no network in unit tests")
    def broadcast(self, tx): raise AssertionError


def mk(tmp_path, net="signet", m=M):
    cfg = load_config({"BTC_WALLET_DATA_DIR": str(tmp_path), "BTC_WALLET_NETWORK": net,
                       "BTC_WALLET_MAX_FEE_RATE": "5"})
    write_seed_file(cfg.data_dir / "seed.enc", m, PW, n=2**10)
    return BtcWallet(cfg, PW, client=NoNet())


def test_bip84_vector_testnet_signet(tmp_path):
    w = mk(tmp_path)
    # BIP84 test vector (coin type 1): m/84'/1'/0'/0/0
    assert w.new_address() == "tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl"
    assert w.new_address().startswith("tb1q")


def test_persisted_index_and_reload(tmp_path):
    w = mk(tmp_path); a0 = w.new_address()
    w2 = BtcWallet(w.cfg, PW, client=NoNet())
    assert w2.new_address() != a0


def test_rejects_mainnet_and_own_address(tmp_path):
    w = mk(tmp_path)
    with pytest.raises(WalletError):
        w.parse_address("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    with pytest.raises(WalletError):
        w.parse_address(w.new_address())


def test_fee_rate_and_cap(tmp_path):
    w = mk(tmp_path)
    assert w.fee_rate() == 2
    with pytest.raises(WalletError):
        w.fee_rate(1)


def test_insufficient_funds(tmp_path):
    w = mk(tmp_path, m=new_mnemonic())
    with pytest.raises(Exception) as e:
        w.build("tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl", 1000, 2)
    assert not isinstance(e.value, WalletError)  # valid address, BDK refuses: no funds


def test_new_mnemonic_is_12_words():
    assert len(new_mnemonic().split()) == 12
