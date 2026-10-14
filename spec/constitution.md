# Constitution — btc-wallet-mcp
1. **Veiligheid boven gemak.** Bij twijfel: weigeren.
2. **Geen eigen crypto.** Sleutels/descriptors/signing via BDK (`bdkpython`); seed-encryptie via `cryptography` (scrypt + AES-256-GCM).
3. **Klein.** Alleen Bitcoin, alleen BIP84 (native segwit), één account. Geen Lightning, geen altcoins.
4. **Test-netwerk standaard.** Mainnet alleen met expliciete dubbele opt-in.
5. **Mens beslist over geld.** Geen uitgave zonder per-betaling menselijk goedkeuringscode die de agent niet kent.
6. **Geheimen verlaten het proces nooit** via tools, logs of foutmeldingen.
7. MIT-licentie, open source, reproduceerbaar te testen.
