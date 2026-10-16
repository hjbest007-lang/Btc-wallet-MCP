# Security policy

wallet-mcp is experimental and unaudited. Do not use it with mainnet funds you cannot lose. The hot wallet should only hold what you are willing to lose if a bug or a bad approval slips through. The cold seed stays on the owner's machine.

## Reporting a vulnerability

Use GitHub private vulnerability reporting (Security tab, "Report a vulnerability"). Include steps to reproduce and the affected version or commit. Do not disclose publicly until a fix is available.

## Scope

In scope:

- the central approval gate (HMAC codes, confirm-id binding, lockout, single use) for every coin
- limit enforcement, including a limit increase that was not approved by the human
- the configure UI or CLI enabling a spend, a mainnet coin, or a higher limit without the human path
- any way for an adapter to broadcast without the gate, or to sign a cold / watch-only wallet
- seed encryption at rest, and secret leakage via tools, logs, or error messages
- verification of the unsigned payload (PSBT, EVM transaction, XRP payment, Litecoin transaction) before signing

The Bitcoin-only server (`btc-wallet-mcp`) is in scope on the same terms.
