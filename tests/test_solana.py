"""Experimental Solana adapter. Mocked send only; nothing hits a cluster."""
import pytest

from wallet_mcp.adapters.base import AdapterError
from wallet_mcp.adapters.solana import SolanaAdapter, keypair_from_mnemonic
from wallet_mcp.coins import COINS
from wallet_mcp.gate import PaymentGate
from wallet_mcp.policy import Policy, approval_code

SECRET = "human-only-secret-123"
MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
ADDRESS = "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"
OTHER = "EHqmfkN89RJ7Y33CXM6uCzhVeuywHoJXZZLszBHHZy7o"


class Rpc:
    def balance(self, _address):
        return 2_000_000_000

    def blockhash(self):
        return "11111111111111111111111111111111"

    def fee(self):
        return 5_000


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, raw):
        self.calls.append(raw)
        return "sig"


def test_solana_vector_mocked_send_and_limits(tmp_path):
    assert str(keypair_from_mnemonic(MNEMONIC).pubkey()) == ADDRESS
    assert COINS["sol"].status == "experimental"
    assert COINS["sol"].default_network == "devnet"
    adapter = SolanaAdapter(
        network="devnet", role="hot", address=ADDRESS, rpc=Rpc(), max_fee_rate=100_000, mnemonic=MNEMONIC,
    )
    spy = Spy()
    policy = Policy("sol", "devnet", "lamport", tmp_path, lambda: (10_000_000, 50_000_000, frozenset()))
    gate = PaymentGate({"sol": adapter}, {"sol": policy}, {"sol": spy}, lambda: SECRET)
    over = gate.prepare_payment("sol", OTHER, 20_000_000)
    assert "per-tx" in over["error"] and spy.calls == []
    prepared = gate.prepare_payment("sol", OTHER, 1_000_000)
    assert prepared["ok"] and prepared["fee"] == 5_000 and spy.calls == []
    assert not gate.send_payment("sol", prepared["confirm_id"], "")["ok"]
    assert spy.calls == []
    code = approval_code(
        SECRET, "sol", "devnet", "hot", prepared["confirm_id"], prepared["address"],
        prepared["amount"], prepared["fee"],
    )
    sent = gate.send_payment("sol", prepared["confirm_id"], code)
    assert sent["ok"] and len(spy.calls) == 1 and isinstance(spy.calls[0], bytes)
    cold = SolanaAdapter(
        network="devnet", role="cold", address=ADDRESS, rpc=Rpc(), max_fee_rate=100_000, mnemonic=None,
    )
    assert cold.can_sign is False
    with pytest.raises(AdapterError, match="cannot sign"):
        cold.sign(adapter.prepare(OTHER, 1), spy)
    assert len(spy.calls) == 1


def test_solana_does_not_replace_execute():
    assert "execute" not in SolanaAdapter.__dict__
