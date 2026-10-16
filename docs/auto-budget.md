# Autonomous spending budget

This is experimental, unaudited software. Read [DISCLAIMER.md](../DISCLAIMER.md) before turning the budget on. Authors and contributors are not liable. Mainnet only with small amounts, and only after your own review.

The owner can let the agent spend up to a small daily amount **without** a per-payment approval code. Anything above that amount still needs `wallet-mcp approve`.

The numbers are **base units of that coin** (satoshis, wei, drops, litoshis, token base units, lamports). There is no price oracle. A budget of 20 000 sats does not track the price of bitcoin.

## What is stored

In each coin's block in `config.json`:

| Field | Meaning |
|---|---|
| `auto_max_per_tx` | Most one autonomous payment may cost, amount plus fee. Default `0` |
| `auto_max_per_day` | Most autonomous spend in the rolling 24 hours, amount plus fee. Default `0` |
| `auto_mac` | HMAC of the approval secret over `automac\|coin\|auto_max_per_tx\|auto_max_per_day`. Empty while both numbers are 0 |

`0` and `0` means the feature is off. A non-zero pair must be less than or equal to `max_per_tx` and `max_per_day`, and `auto_mac` must match. If it does not, the server refuses to load the file and the next spend fails closed.

The spend itself is a row in the existing sealed ledger (`policy/ledger.json`), with `"auto": true`. That row has its own 24-hour counter and also counts toward `max_per_day`. The ledger is reserved under the same `flock` as a normal send, before broadcast, so two payments at once cannot both pass the remaining budget.

## What still applies

Allowlist, fee cap, mainnet opt-in (`WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`), and the address/memo checks. A cold wallet never auto-spends. A token payment (USDC, USDT) is autonomous only when the token amount fits the token budget **and** the parent-chain gas fits the parent coin's budget. Gas is native currency.

Prepare broadcasts when the payment fits. The audit log line has `"auto": true`. The webhook, if set, posts `event: auto_spent`. That call is a notification. It does not contain the approval secret or a code, and a failure does not change the payment. Above the budget, prepare returns a `confirm_id` and the usual approval webhook (`approval_requested`).

## Who can change it

The agent has no MCP tool that writes the budget. The localhost configure page cannot write it either. Lowering (including back to `0` / `0`) applies in one `request-auto` command and reseals `auto_mac`. Raising queues a request for 24 hours. `approve-auto` runs only in an interactive terminal, shows the old and new numbers, and requires the word `yes` plus the approval HMAC.

## Windows cmd

`approve-limit` and `approve-auto` must be an interactive Command Prompt. They refuse a pipe. Type `yes` when asked. If `WALLET_MCP_APPROVAL_SECRET` is not already set, they prompt for it. Do not put that secret in a file the agent can read.

The example raises Bitcoin's hard daily limit first, because 150000 sats is above the default 100000 sat day limit, then grants an autonomous budget of 20000 sats per payment and 150000 sats per day. Replace the ids with the ones the previous command printed.

```bat
set WALLET_MCP_APPROVAL_SECRET=the-same-secret-the-server-uses
wallet-mcp request-limit --coin btc --max-per-tx 50000 --max-per-day 150000
wallet-mcp approve-limit lim_THE_ID_PRINTED_ABOVE
wallet-mcp request-auto --coin btc --auto-max-per-tx 20000 --auto-max-per-day 150000
wallet-mcp approve-auto aut_THE_ID_PRINTED_ABOVE
```

If the Scripts directory is not on `PATH`:

```bat
.\.venv\Scripts\wallet-mcp.exe request-limit --coin btc --max-per-tx 50000 --max-per-day 150000
.\.venv\Scripts\wallet-mcp.exe approve-limit lim_THE_ID_PRINTED_ABOVE
.\.venv\Scripts\wallet-mcp.exe request-auto --coin btc --auto-max-per-tx 20000 --auto-max-per-day 150000
.\.venv\Scripts\wallet-mcp.exe approve-auto aut_THE_ID_PRINTED_ABOVE
```

Skip `request-limit` and `approve-limit` when the autonomous numbers are already within the hard limits. To turn the budget off:

```bat
wallet-mcp request-auto --coin btc --auto-max-per-tx 0 --auto-max-per-day 0
```

That applies immediately and clears `auto_mac`.
