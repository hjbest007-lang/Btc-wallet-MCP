# Specify
**Probleem:** een Grok Bot-agent heeft één Bitcoin-wallet nodig om kleine online betalingen te doen, zonder dat de agent zelfstandig geld kan uitgeven of de seed kan lekken.

**Gebruikers:** de agent (via MCP stdio) en de mens HJB (via CLI: init, approve, backup).

**Tools (MCP):** `get_address`, `get_balance`, `list_transactions`, `prepare_payment(address, amount_sats, memo)`, `send_payment(confirm_id, approval_code)`, `get_config`.

**Acceptatie**
- A1 Default netwerk = signet; `bitcoin` alleen als `BTC_WALLET_NETWORK=bitcoin` én `BTC_WALLET_ALLOW_MAINNET=I_UNDERSTAND_REAL_MONEY`.
- A2 Seed lokaal gegenereerd, versleuteld at rest (passphrase uit env), bestand 0600; geen tool retourneert seed/xprv; logs bevatten geen geheimen.
- A3 `prepare_payment` valideert adres (netwerk), bedrag, limieten (per tx, per 24u rolling, optionele allowlist), max fee-rate, en retourneert onondertekende PSBT + fee + `confirm_id` (verloopt na 10 min).
- A4 `send_payment` vereist approval-code = HMAC-SHA256(approval-secret, payment-details) die alleen de mens kan berekenen; ongeldige code → weiger; na 5 fouten lock. Limieten opnieuw gecontroleerd vlak vóór ondertekenen. Eenmalig gebruik.
- A5 Esplora-URL configureerbaar (mempool.space / blockstream).
- A6 Unit-tests offline; signet-integratietest (opt-in) zonder fondsen.

**Buiten scope:** Lightning/BOLT, multisig, hardware wallets, meerdere accounts, RBF/CPFP-tools, publicatie.
