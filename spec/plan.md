# Plan
- Python 3.11+, `mcp` (FastMCP, stdio), `bdkpython` 2.2 (BDK: BIP39/BIP84, coin selection, signing, Esplora-client), `cryptography`.
- Modules: `config.py` (env → Config, mainnet-guard), `keystore.py` (scrypt+AES-GCM seed-bestand), `policy.py` (limieten, ledger, approval-HMAC, lockout), `wallet.py` (BDK-wrapper, sqlite-cache met alleen publieke data), `server.py` (MCP-tools), `cli.py` (init/approve/backup/status).
- Data dir `~/.btc-wallet-mcp/<network>/`: `seed.enc` (0600), `wallet.sqlite` (publiek), `ledger.json` (uitgaven), `pending.json`.
- Approval-secret los van seed-passphrase (`BTC_WALLET_APPROVAL_SECRET`), alleen in de omgeving van de server en van de mens, nooit in de agent-context.
- Risico's: agent leest env/bestanden via andere tools (shell) → mitigatie in dreigingsmodel: server draaien onder ander OS-account / andere machine.
- Verificatie: pytest (offline), `BTC_WALLET_INTEGRATION=1 pytest -m integration` tegen mempool.space/signet.
