# wallet-mcp

> **Disclaimer — experimental, unaudited software. Use at your own risk.**
> There is **no warranty** (see the [MIT license](LICENSE)). This is **not**
> financial, legal, or tax advice. You are responsible for your keys, seeds,
> limits, and for complying with local law, including tax and AML/KYC.
> **Funds can be lost** because of bugs, AI-agent mistakes, prompt injection,
> or a compromised system. **Mainnet only with small amounts, and only after
> your own review.** Authors and contributors are **not liable**.
> Full text: [DISCLAIMER.md](DISCLAIMER.md).
>
> *Nederlandse samenvatting: experimentele, niet-geauditeerde software; gebruik
> op eigen risico; geen garantie (MIT); geen financieel, juridisch of fiscaal
> advies; jij bent verantwoordelijk voor sleutels, seeds, limieten en naleving
> van lokale wetgeving (belasting, AML/KYC); verlies van funds is mogelijk;
> mainnet alleen met kleine bedragen na eigen review; auteurs en contributors
> zijn niet aansprakelijk.*

Every coin starts on a **test network** (Bitcoin: **signet**). Do not treat a
green test suite as a security review.

A small **multi-coin hot wallet** exposed as an **MCP stdio server**. An AI agent can pay for things online. **By default every payment needs a human approval code.** The owner can grant a small per-coin autonomous budget; a payment inside that budget is sent without a code, and anything above it still needs `wallet-mcp approve`. The agent's spending key is separate from the owner's savings.

The original Bitcoin-only command, `btc-wallet-mcp`, is still there and behaves as before (signet, BIP84, the same approval code). New deployments should use `wallet-mcp`.

## Vision

The agent earns and pays for services on the owner's behalf. It spends from a **hot** key with a low per-payment limit and a low daily limit. The owner tops that key up from a **cold** wallet that never lives on the agent's machine. Amounts above the limit, and any increase of the limit or of the autonomous budget, need a human yes that the agent cannot produce. See [ROADMAP.md](ROADMAP.md). The budget is an integer in the coin's base unit. There is no price oracle.

## Cold and hot, per coin

| | Cold | Hot |
|---|---|---|
| Where the secret lives | Only on the owner's machine | On the server the agent calls |
| What the agent server stores | A watch-only xpub or address | An encrypted hot seed |
| Can the server sign? | No. There is no cold send tool, and cold never auto-spends | Yes, after a per-payment approval code, or inside an owner-granted autonomous budget. Always inside the hard limits |
| What bounds the loss | The cold key is not on this host | The balance the owner deposited, and the daily limit |

Top-ups are ordinary transfers the owner sends from their own cold wallet to the hot receive address. This server does not broadcast those top-ups.

A large cold payment can be signed on the **owner's** machine with a Ledger or Trezor:

```bash
wallet-mcp hw-sign --device ledger --file unsigned.json
```

`unsigned.json` is a PSBT description (Bitcoin, Litecoin) or an EIP-712 document (Ethereum, Avalanche). The command needs a TTY and the approval secret. It then asks the device to sign. The device must be confirmed physically. There is no MCP tool for this, agent mode is refused, and the server does not broadcast the signature. A hot-wallet approval code does not work here.

## Supported coins

| Coin | Status | Default network | Also | Library | Base unit |
|---|---|---|---|---|---|
| Bitcoin | **tested** | signet | testnet, testnet4, regtest, mainnet (opt-in) | bdkpython (BDK, BIP84) | sat |
| Ethereum | **tested** | sepolia | mainnet (opt-in) | eth-account | wei |
| Avalanche C-chain | **tested** | fuji | mainnet (opt-in) | eth-account (chain id 43113 / 43114) | wei |
| XRP | **tested** | testnet | devnet, mainnet (opt-in) | xrpl-py | drop |
| Litecoin | **tested** | testnet | mainnet (opt-in) | embit (BIP84, LTC bech32) | litoshi |
| USDC on Ethereum | **tested** | sepolia | mainnet (opt-in) | eth-account, ERC-20 | 10⁻⁶ USDC |
| USDT on Ethereum | **tested** | sepolia | mainnet (opt-in) | eth-account, ERC-20 (Aave test token on Sepolia) | 10⁻⁶ USDT |
| USDC on Avalanche | **tested** | fuji | mainnet (opt-in) | eth-account, ERC-20 | 10⁻⁶ USDC |
| USDT on Avalanche | **tested** | fuji | mainnet (opt-in) | eth-account, ERC-20 (set `WALLET_MCP_USDT_AVAX_TOKEN` on Fuji) | 10⁻⁶ USDT |
| Solana | **experimental** | devnet | testnet, mainnet (opt-in) | solders (m/44'/501'/0'/0') | lamport |

**Tested** means the offline suite covers address derivation and a mocked send through the central approval gate. XRP destination tags are part of that: known exchange deposit addresses require a tag, and the tag is bound into the approval code. Litecoin covers BIP84 on testnet and mainnet. **Experimental** means the same mocked path exists, but the adapter is new or incomplete. Opt-in testnet reads (`WALLET_MCP_INTEGRATION=1`) never broadcast.

No test broadcasts a transaction. Not on mainnet, and not on testnet either.

Avalanche C-chain uses the same derivation as Ethereum (`m/44'/60'/0'/0/0`), which is what MetaMask uses for C-chain. Litecoin uses `m/84'/1'/0'` on testnet and `m/84'/2'/0'` on mainnet.

## Coins selecteren

Nieuwe gebruikers zetten eerst aan welke coins de agent mag zien. Alleen aangevinkte coins worden als MCP-tools geladen. Staat een coin uit, dan bestaat `eth_prepare_payment` niet, ook niet als de code wel in de repository zit.

Interactief (vinkjes in de terminal):

```bash
wallet-mcp configure
```

```
Select coins. Enter a number to toggle, s to save, q to quit.
  1. [x] Bitcoin (BTC) — signet — tested
  2. [ ] Ethereum (ETH) — sepolia — tested
  3. [ ] Avalanche C-chain (AVAX) — fuji — tested
  4. [ ] XRP (XRP) — testnet — tested
  5. [ ] Litecoin (LTC) — testnet — tested
```

Zonder menu, bijvoorbeeld alleen Bitcoin en Ethereum:

```bash
wallet-mcp configure --enable btc,eth
```

Of een pagina op je eigen computer (alleen `127.0.0.1`, geen andere interface):

```bash
wallet-mcp configure --web
# http://127.0.0.1:8765/
```

De pagina en het menu schrijven `config.json`. Ze kunnen een limiet **verlagen**. Een verhoging wordt een verzoek; die gaat pas in nadat jij in een terminal `wallet-mcp approve-limit <id>` doet. De pagina vraagt nooit om het approval-secret.

Daarna de server herstarten. `list_coins` toont wat er echt geladen is.

## Tools

For each enabled coin `<coin>` (`btc`, `eth`, `avax`, `xrp`, `ltc`):

| Tool | What it does |
|---|---|
| `list_coins` / `get_config` | Which coins are on, networks, limits, whether sending is enabled. No secrets |
| `<coin>_hot_get_address` | Hot receive address (the top-up destination) |
| `<coin>_hot_get_balance` | Hot balance in the coin's base unit |
| `<coin>_cold_get_address` / `<coin>_cold_get_balance` | Watch-only cold view. There is no cold send |
| `<coin>_prepare_payment(address, amount, memo)` | Builds a payment and checks limits. Inside the autonomous budget it broadcasts and returns `txid`. Above that budget it sends nothing and returns `confirm_id` |
| `<coin>_send_payment(confirm_id, approval_code)` | Signs and broadcasts a prepared payment that was above the autonomous budget. Requires the human code for that exact payment |
| `btc_list_transactions` | Bitcoin only |

`amount` is an integer in the base unit (sats, wei, drops, litoshis).

### Approval flow

A payment inside the owner's autonomous budget does not use this flow. `prepare_payment` broadcasts it and the webhook is only a notification (`auto_spent`). Everything else:

1. The agent calls `<coin>_prepare_payment` and gets `confirm_id`, amount, and fee.
2. You run, in your own terminal: `wallet-mcp approve --coin btc <confirm_id>`.
   It shows coin, address, amount, and fee, asks for `yes`, and prints a 12-hex code.
   The code is HMAC-SHA256 of the approval secret and `coin|network|hot|confirm_id|address|amount|fee`.
   An XRP destination tag, when present, is appended as `|tag:<n>`. A code without that suffix does not approve a tagged payment. Known exchange deposit addresses (Kraken, Bitstamp, plus `WALLET_MCP_XRP_TAG_REQUIRED`) cannot be prepared without a tag.
3. You give that code to the agent. `<coin>_send_payment` checks it in constant time, re-checks the limits, then signs.
   The code matches one payment only. Five wrong codes lock that coin until `wallet-mcp unlock --coin <coin>`.

The Bitcoin-only CLI `btc-wallet-mcp approve` still uses the original code format for the original server. Codes are not interchangeable between the two servers.

## Install

On a server, `scripts/install.sh` creates a system user `walletmcp` (shell
`nologin`), a systemd unit that runs as that user, and
`/etc/wallet-mcp/approval-secret` mode `0400`. The agent account is not that
user, so it cannot read the secret. `./scripts/install.sh --dry-run` prints
the layout and does not create anything.

Docker Compose in `deploy/` runs the same way: the image user is `walletmcp`,
and the approval secret is a compose secret file mounted only on the wallet
service. It is not an environment variable of the image, and it must not be
given to the agent container.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
export WALLET_MCP_PASSPHRASE='long random passphrase'   # decrypts hot seeds
export WALLET_MCP_APPROVAL_SECRET='another long secret, not the passphrase'
wallet-mcp configure --enable btc
wallet-mcp init --coin btc --role hot                    # TTY only; shows 12 words once
```

Cold wallet, on the **owner's** machine (not the agent host):

```bash
# WALLET_MCP_MODE must not be "agent" here
wallet-mcp init --coin btc --role cold
# writes owner-cold/seed.enc and a public cold-export.json
```

On the agent host:

```bash
export WALLET_MCP_MODE=agent          # refuses to create or keep a cold seed
wallet-mcp import-watch --coin btc --file cold-export.json
```

`import-watch` rejects mnemonics, xprv/tprv strings, and XRP seeds.

### Add to an MCP client (stdio)

Run this process as a **different OS user** from the agent. The agent's config must not contain `WALLET_MCP_APPROVAL_SECRET` or `WALLET_MCP_PASSPHRASE`.

```json
{
  "mcpServers": {
    "wallet": {
      "command": "/path/to/.venv/bin/wallet-mcp",
      "args": ["serve"],
      "env": {
        "WALLET_MCP_MODE": "agent",
        "WALLET_MCP_PASSPHRASE": "…",
        "WALLET_MCP_APPROVAL_SECRET": "… (>=16 chars, NOT known to the agent)",
        "WALLET_MCP_DATA_DIR": "/var/lib/wallet-mcp"
      }
    }
  }
}
```

Put the approval secret in the server user's environment (or type it into `wallet-mcp approve` on that account). Do not put it in the agent's prompt, repo, or MCP client config.

## Configuration

File: `$WALLET_MCP_DATA_DIR/config.json` (default `~/.wallet-mcp/config.json`). One block per coin: `enabled`, `network`, `max_per_tx`, `max_per_day`, `max_fee_rate`, optional `allowlist`, `auto_max_per_tx`, `auto_max_per_day`, and `auto_mac` when the autonomous budget is on. Unknown coins and unknown networks are rejected. Chain ids are not in the file; they come from the code.

| Env | Default | |
|---|---|---|
| `WALLET_MCP_DATA_DIR` | `~/.wallet-mcp` | config, hot seeds, watch files, ledgers |
| `WALLET_MCP_MODE` | `owner` | `agent` refuses cold-seed creation and refuses to start if a cold seed is present |
| `WALLET_MCP_PASSPHRASE` | – | decrypts hot seeds (min 12 chars) |
| `WALLET_MCP_APPROVAL_SECRET` | – | without it, sending is disabled |
| `WALLET_MCP_APPROVAL_WEBHOOK_URL` | – | optional HTTPS (or localhost HTTP) POST when a payment is waiting for approval, and a notification when one was sent inside the autonomous budget (`event` is `auto_spent`). Failure is logged and does not block the payment. The body has no secret and no approval code |
| `WALLET_MCP_<COIN>_ALLOW_MAINNET` | – | must be `I_UNDERSTAND_REAL_MONEY` to enable that coin on mainnet |
| `WALLET_MCP_BTC_ESPLORA_URL` | mempool.space for the network | https required on mainnet |
| `WALLET_MCP_ETH_RPC_URL` / `WALLET_MCP_AVAX_RPC_URL` / `WALLET_MCP_XRP_RPC_URL` / `WALLET_MCP_LTC_ESPLORA_URL` | public testnet endpoints | https, except localhost |

Default hot limits (base units, amount **plus** fee):

| Coin | Per tx | Per day | Fee cap |
|---|---|---|---|
| btc | 50 000 sat | 100 000 sat | 50 sat/vB |
| eth | 0.001 ETH | 0.005 ETH | 0.001 ETH worst-case fee |
| avax | 0.01 AVAX | 0.05 AVAX | 0.001 AVAX worst-case fee |
| xrp | 1 XRP | 5 XRP | 1 000 drops |
| ltc | 50 000 litoshi | 100 000 litoshi | 50 litoshi/vB |
| usdc / usdt | 10 tokens | 50 tokens | parent-chain gas cap (also counted on eth or avax) |
| sol | 0.01 SOL | 0.05 SOL | 100 000 lamports |

A file that raises a ceiling without a valid HMAC is rejected at startup. Lowering a limit does not need the secret. The web UI cannot raise one.

### Autonomous budget

Per coin, `auto_max_per_tx` and `auto_max_per_day` are integers in that coin's **base unit** (sat, wei, drop, litoshi, token base unit, lamport). The default is `0` / `0`, which turns the feature off. There is no price feed: 10 000 sats stays 10 000 sats if the market moves.

Both numbers must be less than or equal to `max_per_tx` and `max_per_day`. A non-zero pair is sealed with `auto_mac`, an HMAC of the approval secret, the coin, and those two numbers (the same idea as `limits_mac`). A file whose numbers do not match `auto_mac` is rejected at startup and the next spend fails closed. The MCP server has no tool that writes these fields. The configure page cannot write them either.

A hot payment whose amount **plus fee** fits `auto_max_per_tx` and the remaining `auto_max_per_day` is reserved on the sealed ledger under the same file lock as a normal send, then broadcast from `prepare_payment`. No approval code is required. The row is marked `auto` and still counts toward `max_per_day`. The audit log records `"auto": true`. The webhook fires as `auto_spent` (a notification). Allowlist, fee cap, mainnet opt-in, and address/memo checks still apply. A cold wallet never takes this path. A token payment also has to fit the parent coin's autonomous budget, because the gas is native currency.

Anything above the budget falls back to `wallet-mcp approve`. Lowering the budget applies immediately and reseals `auto_mac`. Setting both numbers to `0` turns the feature off and clears the MAC. Raising it is a queued request plus a TTY approval, same shape as `approve-limit`.

On Windows, in `cmd.exe`, from the virtualenv Scripts directory (or after `.\.venv\Scripts\activate.bat`). Amounts below are satoshis. `approve-limit` and `approve-auto` must be an interactive window: they refuse a pipe, ask you to type `yes`, and use `WALLET_MCP_APPROVAL_SECRET` from the environment or prompt for it. The first command prints the id the second command needs. This example raises the hard daily cap first, because 150000 is above the default 100000 sat day limit:

```bat
set WALLET_MCP_APPROVAL_SECRET=the-same-secret-the-server-uses
wallet-mcp request-limit --coin btc --max-per-tx 50000 --max-per-day 150000
wallet-mcp approve-limit lim_THE_ID_PRINTED_ABOVE
wallet-mcp request-auto --coin btc --auto-max-per-tx 20000 --auto-max-per-day 150000
wallet-mcp approve-auto aut_THE_ID_PRINTED_ABOVE
```

If `wallet-mcp` is not on `PATH`, call `.\.venv\Scripts\wallet-mcp.exe` with the same arguments. Skip the `request-limit` / `approve-limit` pair when the autonomous numbers are already within the hard limits. More detail is in [docs/auto-budget.md](docs/auto-budget.md).

## Security model

The review notes for an external audit are in [SECURITY.md](SECURITY.md). A code review from 16 October 2026 is written up in [docs/security/AUDIT-2026-10-16.md](docs/security/AUDIT-2026-10-16.md). It is not a substitute for an independent professional audit. Do not treat a green CI run as one.

**Assets:** cold seeds (owner machine only), hot seeds, the funds on the hot wallets, token balances on those hot keys, the approval secret.

The server process should run as its **own OS user** (or its own machine or container). The agent user must not be able to read that user's environment (`/proc/<pid>/environ`), `config.json`, or `seed.enc`. The approval secret is never returned by a tool, never written to the config file, and never logged. Logs go to stderr and contain ids and amounts, not secrets. `audit.log` in the data directory records every prepare, approve, and send attempt the same way (mode `0600`). It does not store codes or secrets.

| Threat | What holds | What remains |
|---|---|---|
| Agent (or prompt injection) tries to pay by itself | Above the autonomous budget, `send_payment` needs a per-payment HMAC. Inside it, prepare reserves the spend under a file lock before broadcast and the total cannot pass `auto_max_per_tx` / `auto_max_per_day`. Five bad codes lock the coin. Addresses and memos cannot contain control characters or bidi marks. Cold wallets never auto-spend | The owner granted a budget larger than they meant. A human who approves a payment they did not read. A failed broadcast still counts against the daily limit and the auto budget |
| Agent tries to spend the cold wallet | Cold tools are watch-only. `execute` refuses a non-hot wallet. Agent mode will not boot if a cold seed file is on disk. A Bitcoin WIF or secret descriptor is rejected as a watch-only import. `hw-sign` is not an MCP tool and refuses agent mode before it talks to a device | Owner copies `owner-cold/` onto the agent host and ignores the warning, or confirms a hardware prompt they did not read |
| Agent raises its own limit or its autonomous budget | Limit increases need `approve-limit` and `limits_mac`. Budget increases need `request-auto` plus TTY `approve-auto` and `auto_mac`. The configure UI and the MCP tools cannot complete either increase. A request expires after 24 hours. A tampered `auto_mac` fails closed | Same OS user as the server, so the agent can read the secret and forge a MAC. Replacing `config.json` with an older file the owner had approved restores that older budget |
| Agent calls a coin that was not selected | That adapter is not registered. The tool name does not exist | – |
| Tampered unsigned payload | The adapter re-checks address, amount, and fee against the approved payment before sign | A bug in a new adapter; keep the "do not override `execute`" test |
| Agent rewrites the spend ledger | The ledger and pending state are HMAC-sealed with the approval secret. A bad MAC fails closed | Deleting the ledger file starts a fresh 24-hour window. Someone who can delete it can usually read the secret too |
| Hostile chain API | Can lie about balances and fees. Cannot redirect a signed payment: outputs are checked locally, and fees above the cap are refused. Chain URLs must be https (http only on localhost), and redirects are refused | Privacy. Run your own node. An https URL on a private network is still allowed for a self-hosted node |
| Accidental mainnet | Disabled until `WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`. Mainnet chain URLs must be https | – |
| Stolen disk | scrypt (N=2^17, bounded) + AES-256-GCM. New files bind the ciphertext to the coin and role | A weak passphrase. Files written before that binding still use the shared tag |

Out of scope: Lightning, multisig, RBF/CPFP, coin control. Hardware signing is owner-side only (Ledger or Trezor); the agent cannot start it, and this server does not broadcast the result.

## Bitcoin-only server

`btc-wallet-mcp` (init, approve, serve, backup) is unchanged: signet by default, `BTC_WALLET_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY` for mainnet, and the original HMAC over `confirm_id|address|amount|fee`. Its data directory is `~/.btc-wallet-mcp`, separate from `~/.wallet-mcp`.

## Tests

```bash
pytest -q                                          # offline; no broadcasts
BTC_WALLET_INTEGRATION=1 pytest -q -m integration  # live signet read of an empty wallet; still no broadcast
WALLET_MCP_INTEGRATION=1 pytest -q -m integration  # XRP testnet fee + Litecoin testnet fee estimate; still no broadcast
```

## Development

Design notes are in `spec/`. How to add a coin is in [CONTRIBUTING.md](CONTRIBUTING.md). The direction of the project is in [ROADMAP.md](ROADMAP.md).
