# Specify
**Probleem:** een agent heeft een multi-coin hot wallet nodig om kleine online betalingen te doen, zonder zelfstandig geld uit te geven, zonder de cold seed te kunnen gebruiken, en zonder limieten te kunnen verhogen.

**Gebruikers:** de agent (MCP stdio, alleen aangevinkte coins) en de eigenaar (CLI: configure, init, import-watch, approve, approve-limit, request-auto, approve-auto).

**Coins:** Bitcoin (BDK, getest), Ethereum en Avalanche C-chain (eth-account, getest), XRP (xrpl-py, getest, destination tags), Litecoin (embit, getest). Uitbreidbaar via een chain adapter.

**Acceptatie**
- A1 Elke coin start op een testnetwerk (Bitcoin: signet). Mainnet alleen als die coin aan staat én `WALLET_MCP_<COIN>_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`.
- A2 Hot seed lokaal, versleuteld, 0600. Cold seed alleen op de eigenaarsmachine; de agent-server heeft hooguit watch-only en weigert te tekenen. Geen tool retourneert seed of secret.
- A3 `prepare` loopt door de centrale gate: adres, bedrag, per-tx-limiet, 24u-limiet inclusief fee. Boven het autonome budget: geen broadcast, retourneert `confirm_id`. Binnen `auto_max_per_tx` en het resterende `auto_max_per_day` (basiseenheden, geen prijs): de gate reserveert de spend onder dezelfde lock en broadcast dan, zonder approval-code. Cold doet dat nooit.
- A4 `send` vereist een HMAC over coin, netwerk, rol, confirm_id, adres, bedrag en fee, behalve de betaling die prepare al autonoom heeft verstuurd. Ongeldige code weigert. Na 5 fouten lock. Limieten opnieuw vlak voor ondertekenen. Eenmalig. Adapters kunnen de gate niet omzeilen: geen broadcaster vóór de HMAC of de auto-reservering, `execute` niet overschrijfbaar, cold kan niet tekenen.
- A5 Alleen aangevinkte coins worden geladen en als tools geregistreerd (`wallet-mcp configure` of de localhost-UI). De UI kan limieten niet verhogen zonder `approve-limit`. De UI en de MCP-tools kunnen het autonome budget niet zetten. Verhogen gaat via `request-auto` en TTY `approve-auto`, verzegeld met `auto_mac`. Verlagen mag meteen. Default is 0.
- A6 Unit tests zijn offline en mocken het send-pad. Geen echte transactie, ook niet op testnet. Bestaande `btc_wallet_mcp`-tests blijven groen.
- A7 Een limiet boven het goedgekeurde plafond zonder geldige HMAC wordt bij het laden geweigerd. Een autonoom budget dat niet bij `auto_mac` past, of dat boven `max_per_tx` / `max_per_day` ligt, ook.

**Buiten scope:** Lightning, multisig, RBF/CPFP, publicatie naar mainnet-fondsen. XRP destination tags zitten in de approval-HMAC. Testnet-integratietests zijn opt-in en broadcasten niet. Hardware-signing (PSBT / EIP-712) is een owner-CLI; de agent kan die niet starten en de server broadcast die handtekening niet.
