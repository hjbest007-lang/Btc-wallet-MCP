# Security policy

btc-wallet-mcp is experimental and unaudited. Do not use it with mainnet funds you cannot lose.

## Reporting a vulnerability
Use GitHub private vulnerability reporting (Security tab, "Report a vulnerability"). Please include steps to reproduce and the affected version/commit. Do not disclose publicly until a fix is available.

## Scope
In scope: the approval flow (HMAC codes, confirm_id binding, lockout), limits and allowlist enforcement, seed encryption at rest, secret leakage via tools or logs, PSBT verification before signing.
