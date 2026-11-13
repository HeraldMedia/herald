"""Wallets and keypairs.

``Keypair`` is the SDK's sr25519/ed25519 keypair (``bittensor.keyfiles.Keypair``):
``Keypair(ss58_address=...)`` for a public key, ``Keypair.create_from_uri("//Alice")`` for a
development key, ``.sign(data)`` and ``.verify(data, signature)``.

``Wallet(name, hotkey, path)`` opens a wallet directory; the on-disk layout
(``<path>/<name>/hotkeys/<hotkey>``, ``<path>/<name>/coldkeypub.txt``) is the one bittensor has
always used. A name, hotkey or path left out comes from BT_WALLET_NAME, BT_WALLET_HOTKEY or
BT_WALLET_PATH, else "default", "default" and ``~/.bittensor/wallets``.
"""

import os

import bittensor as bt
from bittensor.keyfiles import Keypair

__all__ = ["Keypair", "Wallet", "wallet_from_config", "valid_ss58", "default_wallet_path"]


def default_wallet_path() -> str:
    return os.getenv("BT_WALLET_PATH") or os.path.join(os.path.expanduser("~"), ".bittensor",
                                                       "wallets")


def Wallet(name: str = None, hotkey: str = None, path: str = None) -> "bt.Wallet":
    """The wallet `name` with hotkey `hotkey` under `path` (see the module docstring)."""
    return bt.Wallet(
        name=name or os.getenv("BT_WALLET_NAME") or "default",
        hotkey=hotkey or os.getenv("BT_WALLET_HOTKEY") or "default",
        path=path or default_wallet_path(),
    )


def wallet_from_config(config) -> "bt.Wallet":
    """The wallet ``config.wallet`` names (name, hotkey, path)."""
    wallet = config.wallet
    return Wallet(name=wallet.name, hotkey=wallet.hotkey, path=wallet.path)


def valid_ss58(address) -> bool:
    """True for a string that decodes as an SS58 account address."""
    if not isinstance(address, str):
        return False
    try:
        Keypair(ss58_address=address)
    except Exception:
        return False
    return True
