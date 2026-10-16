# wallet-mcp

> **Experimental, unreviewed software.** Every coin starts on a **test network** (Bitcoin: **signet**). It has not had an independent security review. Do not use it on mainnet with real funds until it has been reviewed.

A small **multi-coin hot wallet** exposed as an **MCP stdio server**. An AI agent can pay for things online, but **only after a human approves that exact payment**. The agent's spending key is separate from the owner's savings.

The original Bitcoin-only command, `btc-wallet-mcp`, is still there and behaves as before (signet, BIP84, the same approval code). New deployments should use `wallet-mcp`.

## Vision

The agent earns and pays for services on the owner's behalf. It spends from a **hot** key with a low per-payment limit and a low daily limit. The owner tops that key up from a **cold** wallet that never lives on the agent's machine. Amounts above the limit, and any increase of the limit, need a human yes that the agent cannot produce. See [ROADMAP.md](ROADMAP.md).

## Cold and hot, per coin

| | Cold | Hot |
|---|---|---|
| Where the secret lives | Only on the owner's machine | On the server the agent calls |
| What the agent server stores | A watch-only xpub or address | An encrypted hot seed |
| Can the server sign? | No. There is no cold send tool | Yes, after a per-payment approval code, inside the limits |
| What bounds the loss | The cold key is not on this host | The balance the owner deposited, and the daily limit |

Top-ups are ordinary transfers the owner sends from their own cold wallet to the hot receive address. This server does not build or sign those top-ups.

## Supported coins

| Coin | Status | Default network | Also | Library | Base unit |
|---|---|---|---|---|---|
| Bitcoin | **tested** | signet | testnet, testnet4, regtest, mainnet (opt-in) | bdkpython (BDK, BIP84) | sat |
| Ethereum | **tested** | sepolia | mainnet (opt-in) | eth-account | wei |
| Avalanche C-chain | **tested** | fuji | mainnet (opt-in) | eth-account (chain id 43113 / 43114) | wei |
| XRP | **experimental** | testnet | devnet, mainnet (opt-in) | xrpl-py | drop |
| Litecoin | **experimental** | testnet | mainnet (opt-in) | embit (BIP84, LTC bech32) | litoshi |

**Tested** means the offline suite covers address derivation and a mocked send through the central approval gate. **Experimental** means the same mocked path exists, but the adapter is new or incomplete (XRP has no destination tags; Litecoin address encoding is embit plus the Litecoin HRP, and it is not covered by a public Esplora test).

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
  4. [ ] XRP (XRP) — testnet — experimental
  5. [ ] Litecoin (LTC) — testnet — experimental
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
| `<coin>_prepare_payment(address, amount, memo)` | Builds an unsigned payment, checks limits, returns `confirm_id` and fee. Sends nothing |
| `<coin>_send_payment(confirm_id, approval_code)` | Signs and broadcasts only with the human code for that exact payment |
| `btc_list_transactions` | Bitcoin only |

`amount` is an integer in the base unit (sats, wei, drops, litoshis).

### Approval flow

1. The agent calls `<coin>_prepare_payment` and gets `confirm_id`, amount, and fee.
2. You run, in your own terminal: `wallet-mcp approve --coin btc <confirm_id>`.
   It shows coin, address, amount, and fee, asks for `yes`, and prints a 12-hex code.
   The code is HMAC-SHA256 of the approval secret and `coin|network|hot|confirm_id|address|amount|fee`.
3. You give that code to the agent. `<coin>_send_payment` checks it in constant time, re-checks the limits, then signs.
   The code matches one payment only. Five wrong codes lock that coin until `wallet-mcp unlock --coin <coin>`.

The Bitcoin-only CLI `btc-wallet-mcp approve` still uses the original code format for the original server. Codes are not interchangeable between the two servers.

## Install

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

File: `$WALLET_MCP_DATA_DIR/config.json` (default `~/.wallet-mcp/config.json`). One block per coin: `enabled`, `network`, `max_per_tx`, `max_per_day`, `max_fee_rate`, optional `allowlist`. Unknown coins and unknown networks are rejected. Chain ids are not in the file; they come from the code.

| Env | Default | |
|---|---|---|
| `WALLET_MCP_DATA_DIR` | `~/.wallet-mcp` | config, hot seeds, watch files, ledgers |
| `WALLET_MCP_MODE` | `owner` | `agent` refuses cold-seed creation and refuses to start if a cold seed is present |
| `WALLET_MCP_PASSPHRASE` | – | decrypts hot seeds (min 12 chars) |
| `WALLET_MCP_APPROVAL_SECRET` | – | without it, sending is disabled |
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

A file that raises a ceiling without a valid HMAC is rejected at startup. Lowering a limit does not need the secret. The web UI cannot raise one.

## Security model

**Assets:** cold seeds (owner machine only), hot seeds, the funds on the hot wallets, the approval secret.

The server process should run as its **own OS user** (or its own machine or container). The agent user must not be able to read that user's environment (`/proc/<pid>/environ`), `config.json`, or `seed.enc`. The approval secret is never returned by a tool, never written to the config file, and never logged. Logs go to stderr and contain ids and amounts, not secrets.

| Threat | What holds | What remains |
|---|---|---|
| Agent (or prompt injection) tries to pay by itself | `send_payment` needs a per-payment HMAC. Limits are checked again immediately before sign. Five bad codes lock the coin | A human who approves without reading the address and amount |
| Agent tries to spend the cold wallet | Cold tools are watch-only. `execute` refuses a non-hot wallet. Agent mode will not boot if a cold seed file is on disk | Owner copies `owner-cold/` onto the agent host and ignores the warning |
| Agent raises its own limit | Increases need `approve-limit` and an HMAC stored next to the ceiling. The configure UI cannot complete an increase | Same OS user as the server, so the agent can read the secret |
| Agent calls a coin that was not selected | That adapter is not registered. The tool name does not exist | – |
| Tampered unsigned payload | The adapter re-checks address, amount, and fee against the approved payment before sign | A bug in a new adapter; keep the "do not override `execute`" test |
| Hostile chain API | Can lie about balances and fees. Cannot redirect a signed payment: outputs are checked locally, and fees above the cap are refused | Privacy. Run your own node |
| Accidental mainnet | Disabled until `WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY` | – |
| Stolen disk | scrypt (N=2^17) + AES-256-GCM | A weak passphrase |

Out of scope: Lightning, multisig, hardware wallets, RBF/CPFP, coin control. XRP destination tags are not implemented (see the roadmap).

## Bitcoin-only server

`btc-wallet-mcp` (init, approve, serve, backup) is unchanged: signet by default, `BTC_WALLET_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY` for mainnet, and the original HMAC over `confirm_id|address|amount|fee`. Its data directory is `~/.btc-wallet-mcp`, separate from `~/.wallet-mcp`.

## Tests

```bash
pytest -q                                          # offline; no broadcasts
BTC_WALLET_INTEGRATION=1 pytest -q -m integration  # live signet read of an empty wallet; still no broadcast
```

## Development

Design notes are in `spec/`. How to add a coin is in [CONTRIBUTING.md](CONTRIBUTING.md). The direction of the project is in [ROADMAP.md](ROADMAP.md).
