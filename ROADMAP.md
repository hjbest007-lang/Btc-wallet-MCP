# Roadmap

## Vision

An AI agent should be able to earn and pay for services on the owner's behalf, across more than one chain, without ever holding the owner's savings.

The agent spends from a **hot wallet**: a separate key, a low per-payment limit, and a low daily limit. The most it can lose is the balance the owner chose to put there. Top-ups come from a **cold wallet** that lives only on the owner's machine. The server the agent talks to stores at most a watch-only xpub or address for that cold wallet, and it has no way to make the cold wallet sign.

Anything above the current limit, and any change that raises a limit, needs a human yes. That yes is an approval code (or a terminal confirmation for a limit change) produced where the agent cannot read the approval secret. The owner runs the server as a different OS user from the agent.

Today the server can prepare and, after that yes, send a hot-wallet payment on Bitcoin, Ethereum, Avalanche C-chain, XRP, and Litecoin. Bitcoin, Ethereum, and Avalanche are covered by the offline test suite. XRP and Litecoin are experimental. Nothing in the test suite broadcasts a transaction, including on testnets.

## What "done" looks like for a coin

- A chain adapter with balance, receive address, and prepare → human approve → send.
- The adapter cannot broadcast during prepare, and it cannot sign the cold wallet.
- Default network is a test network. Mainnet needs that coin's `I_UNDERSTAND_REAL_MONEY` flag.
- Offline tests with a mocked broadcaster: a send without a valid approval code is refused, and so is a send over the per-tx or daily limit.
- The coin is listed in the README table as tested or experimental.

## Good first issues

These are sized for a first contribution. Read `CONTRIBUTING.md` before opening a pull request. Do not broadcast real transactions, including on testnets, from CI or from unit tests.

1. **XRP destination tags.** Extend the experimental XRP adapter so `prepare_payment` can take an optional destination tag, bind that tag into the approval HMAC, and reject a signed transaction whose tag does not match. Add a mocked test. This is the main gap before XRP could be considered for "tested".
2. **Explorer links.** Add a `details_url` field to `prepare_payment` for each coin (mempool.space, sepolia explorer, snowtrace testnet, xrpl testnet, litecoinspace) so the human can look at the address before approving. No new network calls.
3. **Low-balance hint.** When `get_config` or `*_hot_get_balance` runs, include `hot_below_one_tx: true` if the hot balance is below `max_per_tx`. Makes top-ups from cold obvious. Test it with a fake balance.
4. **CLI prompt translations.** The operator CLI is English. A `--lang nl` (or gettext) for `approve`, `configure`, and `approve-limit` would help the first users. Keep the approval code format unchanged.
5. **Bitcoin cold-wallet guide.** Write a short doc (not code) showing how to create the cold BIP84 wallet in Sparrow or a hardware wallet and how to copy only the public descriptor into `import-watch`. No seeds in the doc's examples beyond the well-known abandon mnemonic, marked as public.
6. **Local fee quote for EVM.** Teach the EVM read client to prefer `eth_feeHistory` when the fake RPC provides it, and keep refusing when the worst-case fee exceeds `max_fee_rate`. Unit test with a fake only.
7. **Allowlist editor, TTY only.** A `wallet-mcp allowlist --coin btc --set addr` command that requires a TTY. Widening the allowlist (adding an address, or clearing it) must require the same human path as a limit increase. The web UI must not grow an allowlist editor in the same change.
8. **Next experimental chain.** Add one new adapter (for example Solana) behind the checklist in `CONTRIBUTING.md`. Ship it as `experimental` until the mocked approval and limit tests exist. Do not enable it by default.

## Not in scope yet

Lightning, multisig, hardware-wallet signing inside this process, coin control, RBF/CPFP tools, and any path that lets the agent raise its own limits or sign a cold key.
