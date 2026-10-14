import pytest
from btc_wallet_mcp import keystore


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(keystore, "SCRYPT_N", 2**10)
