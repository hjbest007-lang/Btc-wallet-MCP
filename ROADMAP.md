# Roadmap

## Vision

An AI agent should be able to earn and pay for services on the owner's behalf, across more than one chain, without ever holding the owner's savings.

The agent spends from a **hot wallet**: a separate key, a low per-payment limit, and a low daily limit. The most it can lose is the balance the owner chose to put there. Top-ups come from a **cold wallet** that lives only on the owner's machine. The server the agent talks to stores at most a watch-only xpub or address for that cold wallet, and it has no way to make the cold wallet sign.

Anything above the current limit, and any change that raises a limit, needs a human yes. That yes is an approval code (or a terminal confirmation for a limit change) produced where the agent cannot read the approval secret. The owner runs the server as a different OS user from the agent.

Today the server can prepare and, after that yes, send a hot-wallet payment on Bitcoin, Ethereum, Avalanche C-chain, XRP, Litecoin, and on USDC/USDT (ERC-20 on Ethereum and Avalanche). Solana is experimental. XRP destination tags and Litecoin BIP84 are in the offline suite. Large cold payments can be signed on the owner's Ledger or Trezor; the agent cannot start that. The server is meant to run as its own OS user, with the approval secret outside the agent's reach. Prepare, approve, and send attempts are written to `audit.log`, and an optional webhook can fire when a human still has to approve. Nothing in the test suite broadcasts a transaction, including on testnets. Opt-in integration tests may read a public testnet and still do not broadcast.

## What "done" looks like for a coin

- A chain adapter with balance, receive address, and prepare → human approve → send.
- The adapter cannot broadcast during prepare, and it cannot sign the cold wallet.
- Default network is a test network. Mainnet needs that coin's `I_UNDERSTAND_REAL_MONEY` flag.
- Offline tests with a mocked broadcaster: a send without a valid approval code is refused, and so is a send over the per-tx or daily limit.
- The coin is listed in the README table as tested or experimental.

## Already in the tree

Not open for a first issue. Do not reimplement these:

- XRP destination tags, including the known-exchange requirement, bound into the approval HMAC.
- Litecoin BIP84 on testnet and mainnet, with opt-in testnet reads that do not broadcast.
- Owner-side Ledger/Trezor signing (`hw-sign`) for PSBT and EIP-712. No MCP tool, no broadcast.
- Installer and container layout that run as `walletmcp` and keep the approval secret in a mode `0400` file.
- USDC and USDT on Ethereum and Avalanche, and the experimental Solana adapter.
- Audit log and the optional approval webhook.
- The threat model in `SECURITY.md` and the disclaimer in `DISCLAIMER.md`.

## Good first issues

These are sized for a first contribution. Read `CONTRIBUTING.md` before opening a pull request. Label: `good first issue`. Do not broadcast real transactions, including on testnets, from CI or from unit tests. Do not weaken the approval gate, the limit ledger, or the cold/hot split.

1. **Explorer links.** Add a `details_url` field to `prepare_payment` for each coin (mempool.space, sepolia and snowtrace for the chain and for the token contract, xrpl testnet, litecoinspace, solana explorer on devnet) so the human can look at the address before approving. No new network calls. Test that the URL contains the address and that a send without a code still does not broadcast.

2. **Low-balance hint.** When `get_config` or `*_hot_get_balance` runs, include `hot_below_one_tx: true` if the hot balance is below `max_per_tx`. Do the same for a token coin using the token balance, not the native gas balance. Test it with a fake balance.

3. **CLI prompt translations.** The operator CLI is English. A `--lang nl` (or gettext) for `approve`, `configure`, `approve-limit`, and `hw-sign` would help the first users. Keep the approval code format unchanged. The disclaimer text already has a Dutch summary; do not fork that wording by accident.

4. **Cold-wallet guide.** Write a short doc (not code) showing how to create the cold BIP84 wallet in Sparrow or a hardware wallet and how to copy only the public descriptor into `import-watch`. Show the `hw-sign` JSON shape for a PSBT and for EIP-712, and state that the agent cannot run that command. No seeds in the doc's examples beyond the well-known abandon mnemonic, marked as public.

5. **Local fee quote for EVM.** Teach the EVM read client to prefer `eth_feeHistory` when the fake RPC provides it, and keep refusing when the worst-case fee exceeds `max_fee_rate`. Cover a native transfer and an ERC-20 transfer. Unit test with a fake only.

6. **Allowlist editor, TTY only.** A `wallet-mcp allowlist --coin btc --set addr` command that requires a TTY. Widening the allowlist (adding an address, or clearing it) must require the same human path as a limit increase. The web UI must not grow an allowlist editor in the same change. Include one token coin in the test, so a token allowlist cannot be widened from the page either.

7. **Sign the approval webhook.** Add an optional HMAC header over the webhook body, using a secret that is not the payment approval secret (`WALLET_MCP_WEBHOOK_SECRET`). Document that the receiver must check it. A missing or wrong secret on the sender side skips the hook and still returns the `confirm_id`. Test with a fake poster. Do not put either secret in `audit.log`.

8. **Solana devnet read, opt-in.** An integration test behind `WALLET_MCP_INTEGRATION=1` that calls a public devnet method such as `getHealth` or `getBalance` on the derived watch address. It must not call `sendTransaction`. Skip it in the default `pytest` run. Leave Solana marked experimental.

9. **Fuji USDT contract.** `usdt_avax` on Fuji fails closed until `WALLET_MCP_USDT_AVAX_TOKEN` is set. Add a contract address only when you can cite a published deployment (Tether or the chain's official faucet docs). Do not invent an address. Add one mocked test that the override is checksummed and that an unknown network still refuses.

## Not in scope yet

Lightning, multisig, coin control, RBF/CPFP tools, and any path that lets the agent raise its own limits or sign a cold key. Owner-side Ledger/Trezor signing is a separate TTY command and is not available to the agent.
