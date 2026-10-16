"""USDC/USDT go through the same gate. Gas is capped and counted on the parent coin."""
import json

import pytest

from wallet_mcp.adapters.base import AdapterError
from wallet_mcp.adapters.erc20 import Erc20Adapter, encode_transfer, token_contract
from wallet_mcp.adapters.evm import checksum
from wallet_mcp.config import ConfigError, apply_enabled, load_config
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, approval_code
from eth_account import Account

SECRET = "human-only-secret-123"
ETH_MNEMONIC = "test test test test test test test test test test test junk"
ETH_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
RECIPIENT = "0x0000000000000000000000000000000000000001"
USDC = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"


class Rpc:
    def __init__(self, max_fee=1_000_000_000):
        self.max_fee = max_fee

    def token_balance(self, _token, _holder):
        return 50_000_000

    def get_transaction_count(self, _address):
        return 3

    def suggest_fees(self):
        return self.max_fee, 1


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "txid-token"


def _account():
    Account.enable_unaudited_hdwallet_features()
    return Account.from_mnemonic(ETH_MNEMONIC)


def _adapter(rpc=None):
    account = _account()
    return Erc20Adapter(
        coin_id="usdc_eth", network="sepolia", chain_id=11155111, role="hot",
        address=account.address, token=USDC, rpc=rpc or Rpc(), max_fee_rate=10**15,
        private_key=account.key,
    )


def _code(prepared):
    return approval_code(
        SECRET, "usdc_eth", "sepolia", "hot", prepared["confirm_id"], prepared["address"],
        prepared["amount"], prepared["fee"], extra=prepared["approval_extra"],
    )


def test_usdc_vector_and_mocked_send_counts_gas_on_eth(tmp_path):
    assert checksum(token_contract("usdc_eth", "sepolia")) == checksum(USDC)
    assert token_contract("usdc_avax", "fuji").lower() == "0x5425890298aed601595a70ab815c96711a31bc65"
    adapter = _adapter()
    assert adapter.receive_address() == ETH_ADDRESS
    eth = Policy("eth", "sepolia", "wei", tmp_path / "eth", lambda: (10**15, 5 * 10**15, frozenset()))
    usdc = Policy("usdc_eth", "sepolia", "token", tmp_path / "usdc", lambda: (10_000_000, 50_000_000, frozenset()))
    spy = Spy()
    gate = PaymentGate(
        {"usdc_eth": adapter}, {"usdc_eth": usdc, "eth": eth}, {"usdc_eth": spy}, lambda: SECRET,
    )
    prepared = gate.prepare_payment("usdc_eth", RECIPIENT, 1_000_000)
    assert prepared["ok"] and prepared["fee"] == 0 and prepared["network_fee"] == 100_000 * 1_000_000_000
    assert spy.calls == []
    stale = approval_code(
        SECRET, "usdc_eth", "sepolia", "hot", prepared["confirm_id"], prepared["address"],
        prepared["amount"], prepared["fee"],
    )
    assert not gate.send_payment("usdc_eth", prepared["confirm_id"], stale)["ok"]
    assert spy.calls == []
    sent = gate.send_payment("usdc_eth", prepared["confirm_id"], _code(prepared))
    assert sent["ok"] and len(spy.calls) == 1 and spy.calls[0][0] == 2
    assert usdc.spent_last_24h() == 1_000_000
    assert eth.spent_last_24h() == prepared["network_fee"]
    payload = json.loads(prepared["unsigned_payload"])
    assert payload["to"] == checksum(USDC)
    assert payload["value"] == 0
    assert payload["data"] == encode_transfer(RECIPIENT, 1_000_000)


def test_token_over_parent_gas_limit_does_not_broadcast(tmp_path):
    adapter = _adapter()
    eth = Policy("eth", "sepolia", "wei", tmp_path / "eth", lambda: (1000, 1000, frozenset()))
    usdc = Policy("usdc_eth", "sepolia", "token", tmp_path / "usdc", lambda: (10_000_000, 50_000_000, frozenset()))
    spy = Spy()
    gate = PaymentGate(
        {"usdc_eth": adapter}, {"usdc_eth": usdc, "eth": eth}, {"usdc_eth": spy}, lambda: SECRET,
    )
    refused = gate.prepare_payment("usdc_eth", RECIPIENT, 1)
    assert not refused["ok"] and "per-tx" in refused["error"]
    assert spy.calls == []


def test_token_amount_limit_and_cold_and_fee_cap(tmp_path):
    adapter = _adapter()
    eth = Policy("eth", "sepolia", "wei", tmp_path / "eth", lambda: (10**18, 10**18, frozenset()))
    usdc = Policy("usdc_eth", "sepolia", "token", tmp_path / "usdc", lambda: (500_000, 500_000, frozenset()))
    spy = Spy()
    gate = PaymentGate(
        {"usdc_eth": adapter}, {"usdc_eth": usdc, "eth": eth}, {"usdc_eth": spy}, lambda: SECRET,
    )
    assert "per-tx" in gate.prepare_payment("usdc_eth", RECIPIENT, 600_000)["error"]
    assert spy.calls == []
    cold = Erc20Adapter(
        coin_id="usdc_eth", network="sepolia", chain_id=11155111, role="cold",
        address=ETH_ADDRESS, token=USDC, rpc=Rpc(), max_fee_rate=10**15, private_key=None,
    )
    prepared = adapter.prepare(RECIPIENT, 10)
    with pytest.raises(AdapterError, match="cannot sign"):
        cold.sign(prepared, spy)
    assert spy.calls == []
    huge = _adapter(Rpc(max_fee=10**20))
    with pytest.raises(AdapterError, match="fee"):
        huge.prepare(RECIPIENT, 1)


def test_usdt_without_a_fuji_contract_fails_closed():
    with pytest.raises(AdapterError, match="USDT_AVAX_TOKEN"):
        token_contract("usdt_avax", "fuji", {})


def test_enabling_a_token_requires_the_parent_coin(tmp_path):
    env = {
        "WALLET_MCP_DATA_DIR": str(tmp_path),
        "WALLET_MCP_CONFIG": str(tmp_path / "config.json"),
        "WALLET_MCP_APPROVAL_SECRET": SECRET,
    }
    cfg = load_config(env)
    with pytest.raises(ConfigError, match="enable eth"):
        apply_enabled(cfg, {"usdc_eth"})
    apply_enabled(load_config(env), {"eth", "usdc_eth"})
    assert load_config(env).enabled_ids() == ["eth", "usdc_eth"]


def test_erc20_does_not_replace_execute():
    assert "execute" not in Erc20Adapter.__dict__
