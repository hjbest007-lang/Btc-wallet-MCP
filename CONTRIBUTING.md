# Contributing

This is a multi-coin hot wallet behind an MCP server. Every payment needs a human approval code, and every coin goes through the same limit gate. Safety beats features.

The Bitcoin-only command `btc-wallet-mcp` stays as it is. New coin work belongs in `wallet_mcp`.

## Ground rules

- Test networks first. Never broadcast from tests, including to a public testnet. Mock the send path.
- Bitcoin keys, descriptors, and signing stay in BDK (`bdkpython`). Seed encryption stays in `cryptography`.
- Other coins use a maintained library (`eth-account`, `xrpl-py`, `embit`). Do not invent a signature scheme.
- No tool may return a seed, xprv, passphrase, private key, or approval secret.
- Adapters must not override `ChainAdapter.execute`. They implement `sign` and receive a broadcaster only from the payment gate, after approval.
- Cold wallets are watch-only on the agent server. A change that lets the server sign a cold key is not welcome.
- The configure UI may lower limits. It must not raise them without `approve-limit`.
- A change to `policy.py`, `gate.py`, `keystore.py`, or the approval flow needs a test and a short note in the PR about the threat model (see the Security model table in the README).
- Keep dependencies minimal. A new one needs a reason in the PR.

## Dev setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest -q
BTC_WALLET_INTEGRATION=1 pytest -q -m integration   # optional live signet read, no broadcast
```

## How to add a coin adapter

1. Add a `CoinSpec` in `src/wallet_mcp/coins.py`: id, default **test** network, mainnet name, base unit, default limits, and `status="experimental"`.
2. Implement `src/wallet_mcp/adapters/<coin>.py` as a `ChainAdapter`:
   - `balance` and `receive_address` are read-only.
   - `prepare` builds an unsigned payment and does not broadcast. It must not be given a broadcaster.
   - `sign` re-checks the destination, amount, and fee, then calls the broadcaster it was passed.
   - Do not define `execute`. The base class checks the spend authorization and refuses cold wallets.
   - A cold role stores an xpub or address only. `sign` raises `cold watch-only wallet cannot sign`.
3. Register a factory in `src/wallet_mcp/registry.py`. It is called only when the coin is enabled. Return the hot adapter, the cold adapter, and a broadcaster closure. Do not store the broadcaster on the adapter.
4. Teach `material.watch_document` and `cli._validate_import` to export and import watch-only data, and to reject seeds and private keys.
5. Tests, with `urlopen` patched so a real network call fails the test:
   - a stable address (a published vector if one exists);
   - prepare does not call the broadcaster;
   - send without an approval code does not call it;
   - an amount over the per-tx or daily limit does not call it;
   - a valid code calls it once;
   - the cold adapter cannot sign.
6. Leave `status` as `experimental` until those tests exist. Then update the table in the README. Do not flip a coin to `tested` just because the code runs on your machine.
7. Mention the new dependency and the threat-model impact in the PR.

The payment gate, the HMAC, and the limit ledger are not the adapter's job. If you need a new field covered by the approval code (a destination tag, a memo that is actually on-chain), extend `policy.approval_code` and add a test that an old code no longer matches.

## Workflow

1. Open or pick an issue. Starter ideas are listed in [ROADMAP.md](ROADMAP.md).
2. Branch from `main`. Keep the pull request focused.
3. Run `pytest -q` before pushing. CI runs that suite on every PR.
4. Describe what changed, why, and how you tested it.

## Security issues

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability").

## Spec

Larger features start with a short update under `spec/` (constitution, spec, plan).
