import pytest
from btc_wallet_mcp.config import ConfigError, MAINNET_OPT_IN, load_config


def test_default_is_signet(tmp_path):
    c = load_config({"BTC_WALLET_DATA_DIR": str(tmp_path)})
    assert c.network == "signet" and not c.is_mainnet
    assert c.esplora_url == "https://mempool.space/signet/api"


@pytest.mark.parametrize("net", ["bitcoin", "mainnet"])
def test_mainnet_requires_opt_in(net):
    with pytest.raises(ConfigError):
        load_config({"BTC_WALLET_NETWORK": net})
    with pytest.raises(ConfigError):
        load_config({"BTC_WALLET_NETWORK": net, "BTC_WALLET_ALLOW_MAINNET": "yes"})
    assert load_config({"BTC_WALLET_NETWORK": net, "BTC_WALLET_ALLOW_MAINNET": MAINNET_OPT_IN}).is_mainnet


def test_mainnet_https_only():
    with pytest.raises(ConfigError):
        load_config({"BTC_WALLET_NETWORK": "bitcoin", "BTC_WALLET_ALLOW_MAINNET": MAINNET_OPT_IN,
                     "BTC_WALLET_ESPLORA_URL": "http://evil"})


def test_rejects_altcoins_and_bad_ints():
    with pytest.raises(ConfigError):
        load_config({"BTC_WALLET_NETWORK": "litecoin"})
    with pytest.raises(ConfigError):
        load_config({"BTC_WALLET_MAX_SATS_PER_TX": "-1"})


def test_allowlist_and_custom_esplora(tmp_path):
    c = load_config({"BTC_WALLET_ALLOWLIST": "a, b", "BTC_WALLET_ESPLORA_URL": "https://blockstream.info/signet/api/"})
    assert c.allowlist == {"a", "b"} and c.esplora_url.endswith("/api")
