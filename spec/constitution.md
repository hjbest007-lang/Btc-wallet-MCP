# Constitution — wallet-mcp
1. **Veiligheid boven gemak.** Bij twijfel: weigeren.
2. **Geen eigen crypto voor Bitcoin.** Sleutels, descriptors en signing voor Bitcoin via BDK (`bdkpython`). Seed-encryptie via `cryptography` (scrypt + AES-256-GCM). Andere coins gebruiken een gevestigde library (`eth-account`, `xrpl-py`, `embit`); geen zelfgeschreven handtekeningen.
3. **Cold/hot per coin.** De cold seed staat alleen op de machine van de eigenaar. De server die de agent gebruikt heeft hooguit watch-only (xpub of adres) en kan cold nooit laten tekenen. De hot wallet is een aparte sleutel met een lage per-tx- en daglimiet. Het risico is begrensd door het saldo dat de eigenaar erop zet.
4. **Eén gate voor alle coins.** `prepare → menselijke approval → send` zit centraal. Adapters krijgen geen broadcaster tot de gate de code en de limieten heeft gecontroleerd. Een adapter mag `execute` niet overschrijven.
5. **Testnetwerk standaard.** Bitcoin: signet. Overige coins: hun testnet (Sepolia, Fuji, XRPL testnet, Litecoin testnet). Mainnet alleen met een expliciete opt-in per coin (`WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`).
6. **Mens beslist over geld en over hogere limieten.** Geen uitgave zonder per-betaling HMAC die de agent niet kan berekenen. Een limietverhoging vereist dezelfde secret, buiten het bereik van de agent. De configuratie-UI mag limieten alleen verlagen.
7. **Geheimen verlaten het proces nooit** via tools, logs of foutmeldingen.
8. **Geen echte transacties in tests**, ook niet op testnet. Send-paden gebruiken mocks.
9. MIT-licentie, open source, reproduceerbaar te testen.
