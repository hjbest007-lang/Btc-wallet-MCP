# Contributing to btc-wallet-mcp

Thanks for wanting to help. This project is a small, Bitcoin-only wallet exposed as an MCP server, where **every payment needs a human approval code**. Safety beats features, always.

## Ground rules
- Signet/testnet first. Never test with mainnet funds.
- No home-made crypto: keys, descriptors, coin selection and signing stay in BDK (`bdkpython`).
- No tool may ever return the seed, xprv, passphrase or approval secret.
- Any change to `policy.py`, `keystore.py` or the approval flow needs a test and a short note in the PR about the threat-model impact (see the Threat model table in the README).
- Keep dependencies minimal. New deps need a reason in the PR.

## Dev setup
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest -q                                          # offline unit tests
BTC_WALLET_INTEGRATION=1 pytest -q -m integration  # live signet, no funds needed
```

## Workflow
1. Open or pick an issue (look for `good first issue` / `help wanted`).
2. Fork, branch from `main`, keep PRs small and focused.
3. Run `pytest -q` before pushing; CI runs the offline suite on every PR.
4. Describe what changed, why, and how you tested it.

## Security issues
Please do **not** open a public issue for a vulnerability. Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability").

## Spec-driven
Design artifacts live in `spec/` (constitution, spec, plan, tasks). Larger features start with a short spec update there.
