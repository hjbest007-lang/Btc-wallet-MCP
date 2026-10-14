# btc-wallet-mcp

> ⚠️ **Experimental, unreviewed software.** Runs on **signet by default**. It has not had an independent security review — **do not use it on mainnet with real funds until it has been reviewed.**

A minimal **Bitcoin-only** wallet exposed as an **MCP stdio server**, so an AI agent
can pay for things online — but **only with a human approving every single payment**.

- One BIP84 (native segwit, `bc1q…`/`tb1q…`) account. No altcoins, no Lightning.
- **Signet by default.** Mainnet needs an explicit double opt-in.
- Keys, descriptors, coin selection, signing: [BDK](https://bitcoindevkit.org) (`bdkpython`). No home-made crypto.
- Seed encrypted at rest with scrypt + AES-256-GCM (`cryptography`).
- Chain data via a public **Esplora** API (mempool.space / blockstream.info), configurable.
- MIT licensed. **Status: experimental, unaudited. Use only with amounts you can lose.**

## Tools

| Tool | What it does |
|---|---|
| `get_config` | network, limits, today's spend, whether sending is enabled (no secrets) |
| `get_address` | fresh receive address |
| `get_balance` | sync + balance in sats |
| `list_transactions(limit)` | recent transactions |
| `prepare_payment(address, amount_sats, memo)` | validates + builds an **unsigned PSBT**, returns fee and a `confirm_id` (expires in 10 min). Sends nothing. |
| `send_payment(confirm_id, approval_code)` | signs + broadcasts **only** with a valid human approval code for exactly that payment |

### Approval flow
1. Agent calls `prepare_payment` → gets `confirm_id`, amount, fee.
2. Agent asks you. **You** run, in your own terminal: `btc-wallet-mcp approve <confirm_id>`.
   It shows address/amount/fee, asks `yes`, and prints a 12-hex approval code
   = HMAC-SHA256(`BTC_WALLET_APPROVAL_SECRET`, confirm_id|address|amount|fee).
3. You give the code to the agent → `send_payment`. The code is valid for that one payment only;
   it cannot be reused or applied to another amount/address. 5 wrong codes lock sending until `btc-wallet-mcp unlock`.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
export BTC_WALLET_PASSPHRASE='long random passphrase'   # decrypts the seed
btc-wallet-mcp init                                      # interactive TTY only; shows 12 words once
```

### Backup (do this!)
- `init` shows the 12 BIP39 words **once**. Write them on paper, store offline. Never photograph/paste them.
- Recovery: any BIP84 wallet (Sparrow, BlueWallet, …) with those 12 words, no BIP39 passphrase,
  path `m/84'/1'/0'` (test networks) or `m/84'/0'/0'` (mainnet).
- `btc-wallet-mcp backup` (TTY only, needs passphrase) re-displays the words.
- The encrypted `seed.enc` + passphrase is a second backup; losing the passphrase = losing the file.

### Add to an MCP client (stdio)

```json
{
  "mcpServers": {
    "btc-wallet": {
      "command": "/path/to/.venv/bin/btc-wallet-mcp",
      "args": ["serve"],
      "env": {
        "BTC_WALLET_NETWORK": "signet",
        "BTC_WALLET_PASSPHRASE": "…",
        "BTC_WALLET_APPROVAL_SECRET": "… (>=16 chars, NOT known to the agent)",
        "BTC_WALLET_MAX_SATS_PER_TX": "50000",
        "BTC_WALLET_MAX_SATS_PER_DAY": "100000"
      }
    }
  }
}
```

## Configuration (env)

| Var | Default | |
|---|---|---|
| `BTC_WALLET_NETWORK` | `signet` | `signet`, `testnet4`, `testnet`, `regtest`, `bitcoin` |
| `BTC_WALLET_ALLOW_MAINNET` | – | must be `I_UNDERSTAND_REAL_MONEY` for `bitcoin` |
| `BTC_WALLET_ESPLORA_URL` | mempool.space per network | must be https on mainnet |
| `BTC_WALLET_DATA_DIR` | `~/.btc-wallet-mcp` | per-network subdir |
| `BTC_WALLET_PASSPHRASE` | – | seed decryption (min 12 chars) |
| `BTC_WALLET_APPROVAL_SECRET` | – | without it, sending is disabled |
| `BTC_WALLET_MAX_SATS_PER_TX` | 50000 | incl. fee |
| `BTC_WALLET_MAX_SATS_PER_DAY` | 100000 | rolling 24h, incl. fee |
| `BTC_WALLET_MAX_FEE_RATE` | 50 | sat/vB; higher network fee → refuse |
| `BTC_WALLET_ALLOWLIST` | – | comma-separated addresses; if set, only these |
| `BTC_WALLET_CONFIRM_TTL` | 600 | seconds a prepared payment stays valid |

## Threat model

**Assets:** the seed; the funds; the approval secret.

| Threat | Mitigation | Residual risk |
|---|---|---|
| Agent (or prompt injection) tries to spend on its own | `send_payment` needs a per-payment HMAC code only the human can compute; limits per tx/day; optional allowlist; fee-rate cap | Human approves a malicious request without reading it → **read address & amount in the `approve` prompt** |
| Agent reads the seed | No tool returns seed/xprv; seed encrypted at rest (0600); `init`/`backup` refuse non-TTY | If the agent also has a **shell on the same OS account**, it can read env vars (`/proc/<pid>/environ`) and files. **Run the server under a separate OS user / machine / container** whose env the agent cannot read. |
| Approval code brute force | 48-bit code, constant-time compare, lockout after 5 failures | — |
| Replay / substitution | Code binds confirm_id+address+amount+fee; confirm_id single-use, expires; PSBT outputs and fee re-verified before signing | — |
| Malicious / compromised Esplora server | Can lie about balance/fees/history (privacy leak, failed payments); cannot steal: signing is local, fee capped, outputs verified | Use your own Esplora (`BTC_WALLET_ESPLORA_URL`) for privacy |
| Accidental mainnet | Default signet; mainnet needs `BTC_WALLET_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY` + https | — |
| Stolen disk | scrypt(N=2^17) + AES-GCM; strength = passphrase strength | weak passphrase |
| Secrets in logs | logs to stderr contain only ids/amounts/txids, never secrets; exceptions reduced to type names | — |
| Supply chain | Few deps: `bdkpython` (pinned), `mcp<2`, `cryptography` | Pin hashes / review before mainnet |

Out of scope: Lightning, multisig, hardware wallets, RBF/CPFP, coin control, Tor.

## Tests

```bash
pytest -q                                          # offline unit tests
BTC_WALLET_INTEGRATION=1 pytest -q -m integration  # live signet via mempool.space, no funds needed
```
Not yet covered automatically: actual signing + broadcast (needs funded signet coins from a faucet).

## Development
Spec artifacts (constitution, spec, plan, tasks) are in `spec/`.
