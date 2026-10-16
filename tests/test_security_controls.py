"""Controls added by the 2026-10-16 review. No network and no broadcast."""
import pytest
from embit import bip32, bip39
from embit.networks import NETWORKS

from btc_wallet_mcp.guard import GuardError, validate_chain_url
from btc_wallet_mcp.keystore import KeystoreError, read_seed_file, write_seed_file
from wallet_mcp.adapters.base import AdapterError
from wallet_mcp.adapters.bitcoin import assert_public_descriptor
from wallet_mcp.config import ConfigError, approve_limits, load_config, propose_limits
from wallet_mcp.util import format_amount
from wallet_mcp.watch import assert_watch_only

SECRET = "human-only-secret-123"

MN = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
PW = "correct horse battery"


def test_wif_watch_descriptor_is_rejected():
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MN), version=NETWORKS["test"]["xprv"])
    wif = root.derive("m/84'/1'/0'/0/0").key.wif(NETWORKS["test"])
    descriptor = f"wpkh({wif})"
    with pytest.raises(ConfigError, match="private key"):
        assert_watch_only({"kind": "bdk-descriptor", "external": descriptor, "internal": descriptor})
    with pytest.raises(AdapterError, match="private key"):
        assert_public_descriptor(descriptor, "signet")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://evil.example",
    "http://169.254.169.254/latest",
    "https://user:pass@example.com",
    "gopher://example.com",
])
def test_chain_urls_are_restricted(url):
    with pytest.raises(GuardError):
        validate_chain_url(url)


def test_localhost_http_and_https_are_allowed():
    assert validate_chain_url("http://127.0.0.1:3002").startswith("http://127.0.0.1:3002")
    assert validate_chain_url("https://mempool.space/api") == "https://mempool.space/api"
    with pytest.raises(GuardError):
        validate_chain_url("http://127.0.0.1:3002", mainnet=True)


def test_amount_display_is_exact():
    assert format_amount("eth", 10**18 + 3) == "1000000000000000003 wei (1.000000000000000003 ETH)"
    assert format_amount("xrp", 1_500_000) == "1500000 drops (1.500000 XRP)"


def test_seed_context_is_bound_and_scrypt_n_is_capped(tmp_path):
    path = tmp_path / "seed.enc"
    write_seed_file(path, MN, PW, n=2**10, context="eth:hot")
    assert read_seed_file(path, PW, context="eth:hot") == MN
    with pytest.raises(KeystoreError):
        read_seed_file(path, PW, context="btc:hot")
    with pytest.raises(KeystoreError):
        write_seed_file(tmp_path / "other.enc", MN, PW, n=2**30)


def test_limit_request_expires(tmp_path):
    env = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    cfg = load_config(env)
    status, request = propose_limits(cfg, "btc", 80_000, 100_000, now=lambda: 1.0)
    assert status == "pending"
    with pytest.raises(ConfigError, match="expired"):
        approve_limits(load_config(env), request.request_id, "000000000000", SECRET)
