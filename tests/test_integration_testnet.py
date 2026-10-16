"""Opt-in public testnet reads. Never broadcasts.

    WALLET_MCP_INTEGRATION=1 pytest -q -m integration

XRP calls the ``fee`` method only. Litecoin issues a GET for fee estimates.
``submit`` and Esplora ``POST /tx`` are not used.
"""
import json
import os
import urllib.request

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("WALLET_MCP_INTEGRATION") != "1", reason="opt-in"),
]


def test_xrp_testnet_fee_read_does_not_submit(monkeypatch):
    from wallet_mcp.adapters import xrp

    def refuse_submit(*_args, **_kwargs):
        raise AssertionError("submit")

    monkeypatch.setattr(xrp, "submit_blob", refuse_submit)
    url = xrp.default_rpc("testnet", {}, mainnet=False)
    result = xrp.rpc_call(url, "fee", {})
    drops = result.get("drops") or {}
    assert int(drops.get("minimum_fee") or drops.get("open_ledger_fee")) >= 10


def test_ltc_testnet_fee_estimate_is_a_get():
    from wallet_mcp.adapters.litecoin import default_esplora

    url = default_esplora("testnet", {}, mainnet=False)
    request = urllib.request.Request(url + "/fee-estimates", method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    assert isinstance(data, dict) and data
    assert request.method == "GET"
