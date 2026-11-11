# Bitcoin cold wallet: Sparrow or a hardware wallet → watch-only import

The cold Bitcoin wallet never lives on the machine that runs the agent. Its
seed stays in Sparrow (on the owner's machine) or on a hardware wallet, and
the only thing that crosses to the agent host is a **public descriptor** —
enough to watch balances and derive receive addresses, useless for spending.

This guide is the alternative to `wallet-mcp init --coin btc --role cold`:
same `cold-export.json` at the end, but the key is created in Sparrow or on a
hardware device instead of by this tool.

## 1. Create the cold BIP84 wallet (owner machine)

In [Sparrow Wallet](https://sparrowwallet.com):

1. **File → New Wallet**, give it a name (e.g. `cold-btc`), and create it.
2. Add a keystore, choosing where the seed will live:
   - **Hardware wallet:** pick *Connected Hardware Wallet* (or *Airgapped
     Hardware Wallet* for QR-based devices), unlock the device, and let
     Sparrow read the account xpub from it. The seed never leaves the device.
   - **Software wallet on the owner machine:** pick *New or Imported Software
     Wallet → Generate New*, and choose 12 or 24 words. **Write the words on
     paper.** They are shown once; anyone who sees them owns the wallet.
3. Keep the defaults: **Native SegWit (BIP84)**, account 0. That is the wallet
   type this server derives (addresses start with `bc1q`).

> Never create or type the seed on the agent host, and never type it into any
> website. If a machine is online and shared with the agent, treat a software
> seed created on it as hot, not cold — use a hardware wallet there instead.

## 2. Export only the public descriptor

Still in Sparrow, open the wallet's **Settings** and export the wallet
descriptor (File → Export Wallet → *Descriptor*, or the descriptor shown in
the wallet settings). You need the two BIP84 output descriptors — receive
(`/0/*`) and change (`/1/*`) — which look like:

```
wpkh([73c5da0a/84'/0'/0']xpub6CatWdiZ…/0/*)
wpkh([73c5da0a/84'/0'/0']xpub6CatWdiZ…/1/*)
```

If Sparrow shows you a bare account xpub instead, wrap it in that `wpkh([…])`
form yourself: the bracket holds the master fingerprint and the derivation
path `84'/0'/0'`, and the two copies differ only in the trailing `/0/*` vs
`/1/*`. Export **xpub only** — if anything you are looking at starts with
`xprv`, stop: that is a private key and it must never be copied anywhere.

## 3. Build `cold-export.json`

Put the two descriptors in the watch-file format `import-watch` expects:

```json
{
  "kind": "bdk-descriptor",
  "external": "wpkh([73c5da0a/84'/0'/0']xpub6CatWdiZiodmUeTDp8LT5or8nmbKNcuyvz7WyksVFkKB4RHwCD3XyuvPEbvqAQY3rAPshWcMLoP2fMFMKHPJ4ZeZXYVUhLv1VMrjPC7PW6V/0/*)#wc3n3van",
  "internal": "wpkh([73c5da0a/84'/0'/0']xpub6CatWdiZiodmUeTDp8LT5or8nmbKNcuyvz7WyksVFkKB4RHwCD3XyuvPEbvqAQY3rAPshWcMLoP2fMFMKHPJ4ZeZXYVUhLv1VMrjPC7PW6V/1/*)#lv5jvedt"
}
```

> ⚠️ **The example above is a public test vector.** Its descriptors are
> derived from the well-known test mnemonic `abandon` repeated 11 times plus
> `about`, which is public knowledge — the whole world can watch (and sweep)
> that wallet. Never send real bitcoin to it. Your file must contain **your**
> wallet's fingerprint and xpub from step 2. (The `#…` suffix is the
> descriptor checksum; it is optional and BDK accepts the descriptor without
> it.)

## 4. Import on the agent host

Copy **only** `cold-export.json` to the agent host — no seed file, no Sparrow
wallet file — and run:

```bash
export WALLET_MCP_MODE=agent          # refuses to create or keep a cold seed
wallet-mcp import-watch --coin btc --file cold-export.json
```

`import-watch` validates the file before storing it: the kind must be
`bdk-descriptor`, both descriptors must be public, and it rejects mnemonics,
xprv/tprv strings, and secret descriptors. The imported wallet is watch-only:
the cold adapter cannot sign, and agent mode refuses to start if a cold seed
file is present on the host.

## 5. Verify before you fund it

On the agent host, ask for the cold wallet's first receive address
(`btc_cold_get_address`) and compare it character-for-character with the
first address in Sparrow's **Addresses** tab for the same wallet. They must
match exactly. If they differ, the wrong xpub or derivation path was exported
— fix that before sending anything, because funds sent to an address from the
wrong descriptor are not visible (or spendable) from your Sparrow wallet.

## Rules that do not bend

- The seed (or hardware wallet) stays on the owner side. Only the public
  descriptor crosses to the agent host.
- Never copy `owner-cold/` or any `seed.enc` to the agent host.
- The only seed words that may ever appear in an example are the public
  `abandon` test vector above — real seed words are never written in docs,
  issues, chats, or screenshots.
- Spending from cold storage is a separate, deliberate act on the owner's
  machine (see `hw-sign` in the README); the agent can watch, never spend.
