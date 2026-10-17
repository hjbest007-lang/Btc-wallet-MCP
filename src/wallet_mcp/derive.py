"""Standard hot-key paths for one BIP39 mnemonic.

The BIP39 passphrase is empty, so these addresses match the common wallets
that import the same words: Bitcoin and Litecoin native SegWit (BIP84),
Ethereum and Avalanche C-chain (MetaMask's C-chain path), XRP BIP44, and
Solana's Phantom path. The keystore passphrase that encrypts ``master.enc``
is not that BIP39 passphrase.

Ethereum, Avalanche C-chain, and ERC-20 tokens on those chains all use
``m/44'/60'/0'/0/0``. Derived from the same mnemonic, they are the same
address. A separate per-coin seed does not share it.
"""
from __future__ import annotations

from .adapters.evm import DERIVATION as EVM_PATH
from .adapters.solana import DERIVATION as SOL_PATH
from .adapters.xrp import BIP44_PATH as XRP_PATH
from .coins import COINS, TOKEN_PARENTS


def first_address_path(coin_id: str, network: str) -> str:
    """Path of the first key the adapter spends. Network selects the coin type."""
    owner = TOKEN_PARENTS.get(coin_id, coin_id)
    if owner == "btc":
        # BDK BIP84: coin type 0 only on Bitcoin mainnet. Signet, testnet, and regtest use 1.
        coin = "0'" if network == "bitcoin" else "1'"
        return f"m/84'/{coin}/0'/0/0"
    if owner == "ltc":
        from .adapters.litecoin import coin_type_for
        # SLIP-44: Litecoin is coin type 2 on mainnet and the testnet coin type 1 otherwise.
        return f"m/84'/{coin_type_for(network)}/0'/0/0"
    if owner in {"eth", "avax"}:
        return EVM_PATH
    if owner == "xrp":
        return XRP_PATH
    if owner == "sol":
        return SOL_PATH
    raise ValueError(f"unknown coin {coin_id}")


def first_address(coin_id: str, network: str, mnemonic: str) -> str:
    """First receive address for ``coin_id`` on its configured network."""
    if coin_id not in COINS:
        raise ValueError(f"unknown coin {coin_id}")
    owner = TOKEN_PARENTS.get(coin_id, coin_id)
    if owner == "btc":
        from .adapters.bitcoin import first_receive_address
        return first_receive_address(mnemonic, network)
    if owner in {"eth", "avax"}:
        from .adapters.evm import account_from_mnemonic
        return account_from_mnemonic(mnemonic).address
    if owner == "ltc":
        from .adapters.litecoin import address_at
        return address_at(mnemonic, network, 0, 0)
    if owner == "xrp":
        from .adapters.xrp import wallet_from_mnemonic
        return wallet_from_mnemonic(mnemonic).classic_address
    if owner == "sol":
        from .adapters.solana import keypair_from_mnemonic
        return str(keypair_from_mnemonic(mnemonic).pubkey())
    raise ValueError(f"unknown coin {coin_id}")
