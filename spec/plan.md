# Plan
- Python 3.11+, `mcp` (FastMCP, stdio). Bitcoin blijft `bdkpython` 2.2. EVM (Ethereum + Avalanche C-chain) via `eth-account`. USDC/USDT zijn ERC-20 op diezelfde sleutel; gas telt mee op de parent-coin. XRP via `xrpl-py` (destination tag in de HMAC; bekende exchange-adressen verplicht). Litecoin via `embit` met Litecoin bech32-HRP. Solana (experimental) via `solders`. Seed-encryptie blijft `cryptography`.
- `btc_wallet_mcp` blijft de Bitcoin-only server, ongewijzigd gedrag.
- Nieuw pakket `wallet_mcp`: `coins` (catalogus, default testnet, limieten), `config` (bestand + mainnet-vlag per coin + HMAC op verhoogde plafonds), `policy` (ledger, approval, lockout), `gate` (enige spend-pad; broadcaster pas na approval), `adapters/*`, `registry` (alleen enabled coins), `server`, `configure` (checkbox-CLI + localhost-UI), `cli`.
- Data `~/.wallet-mcp/<coin>/<network>/`: `hot/seed.enc`, `cold/watch.json` (geen secrets), `owner-cold/seed.enc` alleen op de eigenaarsmachine, `policy/` voor ledger en pending.
- `WALLET_MCP_MODE=agent` weigert cold-init en start niet als er een cold seed op de host staat.
- Verificatie: `pytest -q` offline. Geen broadcast. Bestaande signet-integratietest blijft opt-in en leest alleen.
