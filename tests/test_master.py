"""One hot BIP39 mnemonic for every enabled coin. No network and no broadcast.

Vectors use the public BIP39 test mnemonic (entropy of 16 zero bytes). Those
addresses are published; the words are not a secret.
"""
import json
import urllib.request

import pytest
from eth_account import Account

from btc_wallet_mcp.keystore import KeystoreError, decrypt_seed, write_seed_file
from wallet_mcp.cli import main
from wallet_mcp.config import ConfigError, apply_enabled, load_config, save_config
from wallet_mcp.derive import first_address, first_address_path
from wallet_mcp.master import (
    MASTER_CONTEXT,
    MasterError,
    confirm_backup,
    generate_mnemonic,
    load_hot_secret,
    master_path,
    prepare_init_all,
    read_master,
    write_master,
)
from wallet_mcp.registry import build_runtime

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
OTHER = "legal winner thank year wave sausage worth useful legal winner thank yellow"
PW = "correct horse battery"
WRONG_PW = "not-the-real-passphrase"
SECRET = "human-only-secret-123"
XRP_FAMILY = "sEdTM1uX8pu2do5XvTnutH6HsouMaM2"

# Published first-address vectors for MNEMONIC with an empty BIP39 passphrase.
VECTORS = {
    ("btc", "bitcoin"): "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    ("btc", "signet"): "tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl",
    ("btc", "testnet"): "tb1q6rz28mcfaxtmd6v789l9rrlrusdprr9pqcpvkl",
    ("btc", "regtest"): "bcrt1q6rz28mcfaxtmd6v789l9rrlrusdprr9pz3cppk",
    ("eth", "sepolia"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("eth", "mainnet"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("avax", "fuji"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("avax", "mainnet"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("usdc_eth", "sepolia"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("usdt_avax", "fuji"): "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    ("ltc", "mainnet"): "ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh",
    ("ltc", "testnet"): "tltc1q6rz28mcfaxtmd6v789l9rrlrusdprr9pesrjxk",
    ("xrp", "testnet"): "rHsMGQEkVNJmpGWs8XUBoTBiAAbwxZN5v3",
    ("xrp", "mainnet"): "rHsMGQEkVNJmpGWs8XUBoTBiAAbwxZN5v3",
    ("sol", "devnet"): "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk",
    ("sol", "mainnet"): "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk",
}

PATHS = {
    ("btc", "bitcoin"): "m/84'/0'/0'/0/0",
    ("btc", "signet"): "m/84'/1'/0'/0/0",
    ("ltc", "mainnet"): "m/84'/2'/0'/0/0",
    ("ltc", "testnet"): "m/84'/1'/0'/0/0",
    ("eth", "sepolia"): "m/44'/60'/0'/0/0",
    ("avax", "fuji"): "m/44'/60'/0'/0/0",
    ("usdc_eth", "mainnet"): "m/44'/60'/0'/0/0",
    ("xrp", "testnet"): "m/44'/144'/0'/0/0",
    ("sol", "devnet"): "m/44'/501'/0'/0'",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


@pytest.fixture
def fast_scrypt(monkeypatch):
    real = write_seed_file

    def wrapped(path, mnemonic, passphrase, *, n=2**17, context=None):
        return real(path, mnemonic, passphrase, n=2**10, context=context)

    monkeypatch.setattr("wallet_mcp.master.write_seed_file", wrapped)


def env_of(tmp_path, **extra):
    base = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_PASSPHRASE": PW,
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    base.update(extra)
    return base


def use_env(monkeypatch, tmp_path, **extra):
    for key, value in env_of(tmp_path, **extra).items():
        monkeypatch.setenv(key, value)


def tty(monkeypatch, on=True):
    monkeypatch.setattr("sys.stdin.isatty", lambda: on)
    monkeypatch.setattr("sys.stdout.isatty", lambda: on)


def enable(tmp_path, coins, **extra):
    cfg = load_config(env_of(tmp_path, **extra))
    apply_enabled(cfg, set(coins))
    return load_config(env_of(tmp_path, **extra))


def answer_words(mnemonic):
    def reader(prompt):
        index = int(prompt.split()[1].rstrip(":")) - 1
        return mnemonic.split()[index]
    return reader


@pytest.mark.parametrize(("coin", "network"), sorted(VECTORS))
def test_abandon_mnemonic_matches_published_first_address(coin, network):
    assert first_address_path(coin, network)
    assert first_address(coin, network, MNEMONIC) == VECTORS[(coin, network)]


@pytest.mark.parametrize(("coin", "network"), sorted(PATHS))
def test_standard_paths(coin, network):
    assert first_address_path(coin, network) == PATHS[(coin, network)]


def test_eth_avax_and_tokens_share_one_address():
    eth = first_address("eth", "sepolia", MNEMONIC)
    assert first_address("avax", "fuji", MNEMONIC) == eth
    assert first_address("usdc_eth", "sepolia", MNEMONIC) == eth
    assert first_address("usdt_eth", "mainnet", MNEMONIC) == eth
    assert first_address("usdc_avax", "fuji", MNEMONIC) == eth
    assert first_address("usdt_avax", "mainnet", MNEMONIC) == eth


def test_xrp_bip44_key_signs_without_a_family_seed():
    from xrpl.models.transactions import Payment
    from xrpl.transaction import sign
    from wallet_mcp.adapters.xrp import wallet_from_mnemonic

    wallet = wallet_from_mnemonic(MNEMONIC)
    assert wallet.classic_address == VECTORS[("xrp", "testnet")]
    assert wallet.seed is None
    signed = sign(Payment(
        account=wallet.classic_address,
        destination="rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        amount="10", fee="12", sequence=1,
    ), wallet)
    assert signed.blob().startswith("120")


def test_confirm_asks_three_distinct_words_and_rejects_a_typo():
    phrase = generate_mnemonic(12)
    prompts = []

    def reader(prompt):
        prompts.append(prompt)
        return answer_words(phrase)(prompt)

    confirm_backup(phrase, reader)
    assert len(prompts) == 3
    assert len(set(prompts)) == 3
    with pytest.raises(MasterError, match="not saved"):
        confirm_backup(phrase, lambda _prompt: "notaword")
    # The check is the word, not the capitalisation or surrounding spaces.
    words = phrase.split()
    confirm_backup(phrase, lambda prompt: " " + words[int(prompt.split()[1].rstrip(":")) - 1].upper() + " ")


def test_init_all_refuses_cold_without_writing(tmp_path, monkeypatch):
    use_env(monkeypatch, tmp_path)
    enable(tmp_path, ["btc", "eth"])
    tty(monkeypatch, True)
    with pytest.raises(SystemExit, match="cold"):
        main(["init", "--all", "--role", "cold"])
    assert not master_path(load_config(env_of(tmp_path))).exists()
    assert not (tmp_path / "btc").exists()


def test_init_all_refuses_a_non_tty(tmp_path, monkeypatch, capsys):
    use_env(monkeypatch, tmp_path)
    enable(tmp_path, ["btc"])
    tty(monkeypatch, False)
    with pytest.raises(SystemExit, match="interactive terminal"):
        main(["init", "--all", "--role", "hot"])
    assert not (tmp_path / "master.enc").exists()
    captured = capsys.readouterr()
    assert "Shown once" not in captured.out
    assert "abandon" not in captured.out


def test_init_all_refuses_an_existing_hot_seed_unless_skip(tmp_path, monkeypatch, fast_scrypt):
    use_env(monkeypatch, tmp_path)
    cfg = enable(tmp_path, ["btc", "eth"])
    write_seed_file(cfg.hot_dir("eth") / "seed.enc", OTHER, PW, n=2**10, context="eth:hot")
    tty(monkeypatch, True)
    with pytest.raises(SystemExit, match="skip-existing"):
        main(["init", "--all", "--role", "hot"])
    assert not (tmp_path / "master.enc").exists()
    # A seed for a coin that is not enabled does not block the others.
    cfg.raw["coins"]["eth"]["enabled"] = False
    save_config(cfg)
    prompts = []

    def wrapped(mnemonic, _reader):
        from wallet_mcp.master import confirm_backup as real
        prompts.append(mnemonic)
        real(mnemonic, answer_words(mnemonic))

    monkeypatch.setattr("wallet_mcp.cli.confirm_backup", wrapped)
    main(["init", "--all", "--role", "hot"])
    assert (tmp_path / "master.enc").exists()
    assert len(prompts) == 1 and len(prompts[0].split()) == 24


def test_skip_existing_keeps_the_coin_seed_ahead_of_the_master(tmp_path, monkeypatch, fast_scrypt):
    use_env(monkeypatch, tmp_path)
    cfg = enable(tmp_path, ["btc", "eth", "xrp"])
    write_seed_file(cfg.hot_dir("eth") / "seed.enc", OTHER, PW, n=2**10, context="eth:hot")
    write_seed_file(cfg.hot_dir("xrp") / "seed.enc", XRP_FAMILY, PW, n=2**10, context="xrp:hot")
    tty(monkeypatch, True)

    def wrapped(mnemonic, _reader):
        from wallet_mcp.master import confirm_backup as real
        real(mnemonic, answer_words(mnemonic))

    monkeypatch.setattr("wallet_mcp.cli.confirm_backup", wrapped)
    main(["init", "--all", "--role", "hot", "--skip-existing"])
    cfg = load_config(env_of(tmp_path))
    eth = load_hot_secret(cfg, "eth")
    btc = load_hot_secret(cfg, "btc")
    xrp = load_hot_secret(cfg, "xrp")
    assert eth.source == "coin" and eth.value == OTHER
    assert btc.source == "master" and len(btc.value.split()) == 24
    assert xrp.source == "coin" and xrp.value == XRP_FAMILY
    Account.enable_unaudited_hdwallet_features()
    runtime = build_runtime(cfg)
    assert runtime.hot["eth"].receive_address() == Account.from_mnemonic(OTHER).address
    assert runtime.hot["eth"].receive_address() != VECTORS[("eth", "sepolia")]
    assert runtime.hot["btc"].receive_address() == first_address("btc", "signet", btc.value)
    from xrpl.wallet import Wallet
    assert runtime.hot["xrp"].receive_address() == Wallet.from_seed(XRP_FAMILY).classic_address
    assert runtime.hot["xrp"].receive_address() != VECTORS[("xrp", "testnet")]


def test_master_aad_and_wrong_passphrase_fail_closed(tmp_path):
    cfg = enable(tmp_path, ["eth", "btc"])
    write_master(cfg, PW, MNEMONIC, n=2**10)
    path = master_path(cfg)
    blob = json.loads(path.read_text())
    assert blob["context"] == MASTER_CONTEXT
    assert "abandon" not in path.read_text()
    assert path.stat().st_mode & 0o077 == 0
    assert decrypt_seed(blob, PW, context=MASTER_CONTEXT) == MNEMONIC
    with pytest.raises(KeystoreError):
        decrypt_seed(blob, PW, context="eth:hot")
    with pytest.raises(KeystoreError):
        decrypt_seed(blob, PW, context="btc:hot")
    with pytest.raises(KeystoreError):
        decrypt_seed(blob, PW, context=None)
    with pytest.raises(KeystoreError, match="wrong passphrase") as err:
        decrypt_seed(blob, WRONG_PW, context=MASTER_CONTEXT)
    assert "abandon" not in str(err.value)
    cfg.env["WALLET_MCP_PASSPHRASE"] = WRONG_PW
    with pytest.raises(ConfigError, match="wrong passphrase") as closed:
        load_hot_secret(cfg, "btc")
    assert "abandon" not in str(closed.value)
    # The file is unchanged and still opens with the real passphrase.
    cfg.env["WALLET_MCP_PASSPHRASE"] = PW
    assert load_hot_secret(cfg, "btc").value == MNEMONIC


def test_bad_coin_seed_does_not_fall_back_to_the_master(tmp_path):
    cfg = enable(tmp_path, ["eth"])
    write_seed_file(cfg.hot_dir("eth") / "seed.enc", OTHER, PW, n=2**10, context="eth:hot")
    write_master(cfg, PW, MNEMONIC, n=2**10)
    cfg.env["WALLET_MCP_PASSPHRASE"] = WRONG_PW
    with pytest.raises(ConfigError, match="wrong passphrase") as err:
        load_hot_secret(cfg, "eth")
    assert "legal" not in str(err.value) and "abandon" not in str(err.value)
    cfg.env["WALLET_MCP_PASSPHRASE"] = PW
    # Right passphrase still prefers the per-coin file, including for a token.
    assert load_hot_secret(cfg, "eth").source == "coin"
    assert load_hot_secret(cfg, "usdc_eth").value == OTHER


def test_swapped_context_does_not_open_a_coin_seed(tmp_path):
    cfg = enable(tmp_path, ["eth"])
    path = cfg.hot_dir("eth") / "seed.enc"
    write_seed_file(path, OTHER, PW, n=2**10, context="eth:hot")
    blob = json.loads(path.read_text())
    with pytest.raises(KeystoreError):
        decrypt_seed(blob, PW, context=MASTER_CONTEXT)
    with pytest.raises(KeystoreError):
        read_master(path, PW)


def test_init_all_derives_every_enabled_coin_on_its_configured_network(tmp_path, monkeypatch, fast_scrypt, capsys):
    use_env(
        monkeypatch, tmp_path,
        WALLET_MCP_USDT_AVAX_TOKEN="0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    )
    coins = ["btc", "eth", "avax", "xrp", "ltc", "usdc_eth", "usdt_eth", "usdc_avax", "usdt_avax", "sol"]
    enable(tmp_path, coins, WALLET_MCP_USDT_AVAX_TOKEN="0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7")
    tty(monkeypatch, True)

    def wrapped(mnemonic, _reader):
        from wallet_mcp.master import confirm_backup as real
        real(mnemonic, answer_words(mnemonic))

    monkeypatch.setattr("wallet_mcp.cli.confirm_backup", wrapped)
    main(["init", "--all", "--role", "hot", "--words", "12"])
    out = capsys.readouterr().out
    cfg = load_config(env_of(
        tmp_path, WALLET_MCP_USDT_AVAX_TOKEN="0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    ))
    phrase = read_master(master_path(cfg), PW)
    assert len(phrase.split()) == 12
    assert out.count(phrase) == 1
    assert "DISCLAIMER.md" in out
    for coin in coins:
        assert not (cfg.hot_dir(coin) / "seed.enc").exists()
    runtime = build_runtime(cfg)
    defaults = {
        "btc": "signet", "eth": "sepolia", "avax": "fuji", "xrp": "testnet", "ltc": "testnet",
        "usdc_eth": "sepolia", "usdt_eth": "sepolia", "usdc_avax": "fuji", "usdt_avax": "fuji",
        "sol": "devnet",
    }
    for coin, network in defaults.items():
        assert runtime.hot[coin].receive_address() == first_address(coin, network, phrase)
    assert runtime.hot["eth"].receive_address() == runtime.hot["avax"].receive_address()
    assert runtime.hot["usdc_eth"].receive_address() == runtime.hot["eth"].receive_address()
    assert runtime.hot["usdt_avax"].receive_address() == runtime.hot["avax"].receive_address()


def test_mainnet_coin_type_follows_the_configured_network(tmp_path):
    cfg = enable(
        tmp_path, ["btc", "ltc"],
        WALLET_MCP_BTC_ALLOW_MAINNET="I_UNDERSTAND_REAL_MONEY",
        WALLET_MCP_LTC_ALLOW_MAINNET="I_UNDERSTAND_REAL_MONEY",
    )
    cfg.raw["coins"]["btc"]["network"] = "bitcoin"
    cfg.raw["coins"]["ltc"]["network"] = "mainnet"
    save_config(cfg)
    cfg = load_config(env_of(
        tmp_path,
        WALLET_MCP_BTC_ALLOW_MAINNET="I_UNDERSTAND_REAL_MONEY",
        WALLET_MCP_LTC_ALLOW_MAINNET="I_UNDERSTAND_REAL_MONEY",
    ))
    write_master(cfg, PW, MNEMONIC, n=2**10)
    runtime = build_runtime(cfg)
    assert runtime.hot["btc"].receive_address() == VECTORS[("btc", "bitcoin")]
    assert runtime.hot["ltc"].receive_address() == VECTORS[("ltc", "mainnet")]


def test_existing_btc_seed_file_still_opens(tmp_path):
    cfg = enable(tmp_path, ["btc"])
    write_seed_file(cfg.hot_dir("btc") / "seed.enc", MNEMONIC, PW, n=2**10, context="btc:hot")
    runtime = build_runtime(cfg)
    assert runtime.hot["btc"].receive_address() == VECTORS[("btc", "signet")]
    assert load_hot_secret(cfg, "btc").source == "coin"


def test_backup_all_is_tty_only_and_fails_closed(tmp_path, monkeypatch, capsys):
    use_env(monkeypatch, tmp_path)
    cfg = enable(tmp_path, ["btc"])
    write_master(cfg, PW, MNEMONIC, n=2**10)
    tty(monkeypatch, False)
    with pytest.raises(SystemExit, match="interactive terminal"):
        main(["backup", "--all"])
    assert MNEMONIC not in capsys.readouterr().out
    tty(monkeypatch, True)
    with pytest.raises(SystemExit, match="hot key"):
        main(["backup", "--all", "--role", "cold"])
    monkeypatch.setenv("WALLET_MCP_PASSPHRASE", WRONG_PW)
    with pytest.raises(SystemExit, match="wrong passphrase") as err:
        main(["backup", "--all"])
    captured = capsys.readouterr()
    assert MNEMONIC not in captured.out and "abandon" not in str(err.value)
    monkeypatch.setenv("WALLET_MCP_PASSPHRASE", PW)
    main(["backup", "--all"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == MNEMONIC


def test_backup_coin_shows_the_per_coin_secret_not_the_master(tmp_path, monkeypatch, capsys):
    use_env(monkeypatch, tmp_path)
    cfg = enable(tmp_path, ["eth"])
    write_seed_file(cfg.hot_dir("eth") / "seed.enc", OTHER, PW, n=2**10, context="eth:hot")
    write_master(cfg, PW, MNEMONIC, n=2**10)
    tty(monkeypatch, True)
    main(["backup", "--coin", "eth", "--role", "hot"])
    shown = capsys.readouterr().out.strip().splitlines()[-1]
    assert shown == OTHER
    assert MNEMONIC not in shown


def test_failed_backup_check_does_not_write_the_master(tmp_path, monkeypatch, capsys):
    use_env(monkeypatch, tmp_path)
    enable(tmp_path, ["btc", "eth"])
    tty(monkeypatch, True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "wrong")
    with pytest.raises(SystemExit, match="not saved"):
        main(["init", "--all", "--role", "hot"])
    assert not (tmp_path / "master.enc").exists()
    out = capsys.readouterr().out
    assert "Shown once" in out
    assert out.count("Shown once") == 1


def test_prepare_refuses_when_no_coin_is_enabled(tmp_path):
    cfg = load_config(env_of(tmp_path))
    with pytest.raises(MasterError, match="no coins"):
        prepare_init_all(cfg, skip_existing=False)


def test_words_flag_is_only_for_all(tmp_path, monkeypatch):
    use_env(monkeypatch, tmp_path)
    enable(tmp_path, ["btc"])
    with pytest.raises(SystemExit, match="--words"):
        main(["init", "--coin", "btc", "--role", "hot", "--words", "12"])
    with pytest.raises(SystemExit):
        main(["init", "--all", "--role", "hot", "--words", "15"])
