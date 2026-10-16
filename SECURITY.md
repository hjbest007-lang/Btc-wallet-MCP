# Security

wallet-mcp is experimental and has not had an independent audit. Read
[DISCLAIMER.md](DISCLAIMER.md) before using it. Do not put mainnet funds you
cannot lose in a hot wallet. The cold seed stays on the owner's machine.

This document is the threat model an external reviewer should start from. It
describes what the software is trying to hold, and what it is not.

## Reporting a vulnerability

Use GitHub private vulnerability reporting (Security tab, "Report a
vulnerability"). Include steps to reproduce and the affected version or commit.
Do not disclose publicly until a fix is available.

## Assets

| Asset | Where it should live |
|---|---|
| Cold seed or hardware key | Owner machine, or a Ledger/Trezor. Never on the agent host |
| Hot seed | Encrypted file (`seed.enc`) readable by the server OS user only |
| Approval secret | Server OS user's environment or a mode `0400` file. Not in the agent config, the repo, or the MCP client |
| Seed passphrase | Same boundary as the approval secret. It is a different value |
| Hot-wallet funds | Only what the owner deposited, inside the per-tx and daily limits |
| Token balances (USDC/USDT) | Same hot key as the parent chain. Gas is native and is also counted on the parent ledger |
| Audit log | `$WALLET_MCP_DATA_DIR/audit.log`, mode `0600` |

## Actors

- **Owner.** Runs `configure`, `init`, `import-watch`, `approve`, `approve-limit`, and `hw-sign` in a TTY. Holds the approval secret and the cold key.
- **Agent.** Talks to the MCP stdio server. Can be prompt-injected. Must not learn the approval secret or a cold key.
- **Server process.** The `walletmcp` OS user. Can sign hot keys after the gate says yes. Cannot sign cold keys.
- **Chain endpoints.** Esplora, Ethereum/Avalanche RPC, XRP, Solana. They can lie about balances and fees. They cannot change a transaction the process already checked locally.
- **Operator webhook.** Optional HTTPS endpoint told that an approval is waiting. Its response is ignored.
- **Someone with the disk or the same OS user.** Can read what that user can read. scrypt + AES-256-GCM is the control for a stolen disk, not for a same-user attacker.

## Trust boundaries

1. Agent process versus server process. Different OS users. The agent must not read `/proc/<pid>/environ`, `config.json`, `seed.enc`, or `approval-secret`.
2. Server versus cold key. The server stores a watch-only xpub or address. `WALLET_MCP_MODE=agent` refuses to boot if a cold seed file is present.
3. Prepare versus send. Prepare has no broadcaster. Send receives one only inside `PaymentGate.send_payment`, after the HMAC check and a second limit check.
4. Hot spend versus hardware spend. Hardware signing is a TTY command on the owner machine. It is not an MCP tool. It refuses agent mode before it talks to a device, and it does not broadcast.

## What the gate has to enforce

These are the invariants a review should try to break:

- Every coin, including USDC, USDT, and Solana, spends only through `PaymentGate`. Adapters do not define `execute`.
- `SpendAuthorization` can be constructed only with the seal object in `gate.py`, and only for `role=hot`.
- The approval HMAC covers coin, network, role, confirm id, address, amount, and fee. An XRP destination tag is appended as `|tag:<n>`. Token gas is appended as `|gas:<wei>`. A code that omits the suffix does not match.
- A code is single-use. Five bad codes lock that coin until `wallet-mcp unlock`.
- Limits are checked again immediately before sign. A token payment's gas is also checked against the parent coin (ETH or AVAX) and then recorded on that coin's ledger.
- Raising a limit requires `approve-limit` and a stored HMAC. The configure UI and the localhost page cannot complete an increase. They never ask for the approval secret.
- Known XRP exchange deposit addresses, `RequireDestTag` accounts, and `WALLET_MCP_XRP_TAG_REQUIRED` cannot be prepared without a destination tag. `sign` rejects a tag that differs from the approved one.
- A cold adapter's `sign` raises `cold watch-only wallet cannot sign`.
- `hw-sign` requires a TTY, refuses `WALLET_MCP_MODE=agent`, uses a separate HMAC (`hw|...|cold|...`), and returns only after the device reports a physical confirmation. Ledger and Trezor classes have no software signing fallback. The server does not broadcast the signature.
- No tool returns a seed, xprv, private key, passphrase, or approval secret. `import-watch` rejects those shapes.
- Mainnet stays off until `WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`.
- The audit log and the webhook body do not contain the approval secret or an approval code. A webhook failure does not change the payment result. The webhook URL must be HTTPS, or HTTP to localhost only.

## Threats and what holds

| Threat | What holds | What remains |
|---|---|---|
| Agent pays by itself, or via prompt injection | `send_payment` needs a per-payment HMAC. Limits are checked again before sign. Five bad codes lock the coin | A human who approves a payment they did not read |
| Agent spends the cold wallet | Cold tools are watch-only. `execute` refuses a non-hot role. Agent mode will not boot if a cold seed is on disk. `hw-sign` is not a tool and refuses agent mode | Owner copies `owner-cold/` onto the agent host, or confirms a hardware prompt they did not read |
| Agent raises its own limit or turns on a coin that was not selected | Increases need `approve-limit`. Disabled coins are not registered as tools | Same OS user as the server, so the agent can read the secret and the config |
| Tampered unsigned payload (PSBT, EVM tx, ERC-20 calldata, XRP payment, Solana transfer) | The adapter re-checks destination, amount, fee, and any bound tag or gas before sign | A bug in a new adapter. Keep the "do not override `execute`" test |
| Missing XRP destination tag to an exchange | Prepare refuses known exchange addresses and `RequireDestTag` accounts | An exchange address that is not in the built-in list and has not set the flag. Add it with `WALLET_MCP_XRP_TAG_REQUIRED` |
| Token transfer that hides a large gas bill | Gas is inside the HMAC and inside the parent coin's daily limit | Parent coin not enabled: the token cannot be enabled without it |
| Agent triggers a Ledger or Trezor | No MCP tool. Agent mode returns before the device is opened. A device that does not report a button press produces no signature | A physically present owner who confirms the wrong prompt |
| Hostile chain API | Can lie about balances and fees. Fees above `max_fee_rate` are refused. Outputs are checked locally | Privacy, and a fee quote inside the cap. Run your own node |
| Webhook or log used to steal the secret | Neither channel is given the secret or the code. A bad webhook cannot approve a payment | The log file if the server user is compromised. It still has no secret |
| Accidental mainnet | Disabled until that coin's opt-in flag is set. Enabling mainnet prints a warning | The owner sets the flag and deposits more than they meant to |
| Stolen disk | scrypt (N=2^17) + AES-256-GCM on hot seeds | A weak passphrase |
| Supply chain | Dependencies are pinned loosely on purpose (`bdkpython` is pinned). A review should re-check `eth-account`, `xrpl-py`, `embit`, `solders`, `cryptography`, and `mcp` | A compromised build of one of those |

## Review guide

Start with these files. A finding that lets an adapter broadcast without `PaymentGate`, sign a cold key, forge `SpendAuthorization`, or skip a limit is in scope even if the coin is marked experimental.

- `src/wallet_mcp/gate.py`, `policy.py`, `auth.py` — the only spend path and the HMAC
- `src/wallet_mcp/adapters/base.py` — `execute` must stay here
- `src/wallet_mcp/registry.py` — broadcasters are constructed here and not stored on the adapter
- `src/wallet_mcp/adapters/xrp.py` — destination tag binding
- `src/wallet_mcp/adapters/erc20.py` — calldata and gas binding
- `src/wallet_mcp/adapters/solana.py` — experimental; same gate
- `src/wallet_mcp/hardware.py` — owner-side device signing, no broadcast
- `src/wallet_mcp/config.py`, `configure.py` — limit raises and the localhost page
- `src/wallet_mcp/audit.py` — redaction and webhook allow-list
- `src/btc_wallet_mcp/` — the original Bitcoin-only server, same approval idea, separate data directory
- `scripts/install.sh`, `deploy/` — OS user and secret file permissions

Tests that protect these invariants live in `tests/test_gate.py`, `tests/test_adapters.py`, `tests/test_tokens.py`, `tests/test_solana.py`, `tests/test_hardware.py`, `tests/test_audit.py`, and `tests/test_install.py`. The default suite does not broadcast, including to a testnet. Opt-in integration tests only read public fee endpoints.

## Out of scope

Lightning, multisig, coin control, RBF/CPFP, and any design that lets the agent sign a cold key or raise its own limits. Hardware signing inside the agent process is out of scope on purpose: the device stays on the owner side.

The authors and contributors accept no liability for loss of funds. See [DISCLAIMER.md](DISCLAIMER.md) and the MIT license.
