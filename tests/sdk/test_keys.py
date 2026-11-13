"""herald.sdk.keys: wallets on disk, ss58 checks and signatures."""

import os
from types import SimpleNamespace

import pytest
from bittensor.keyfiles import Keyfile

from herald.sdk import keys
from herald.validator.news import publish


def _write_hotkey(root, wallet="w", hotkey="h", uri="//HeraldKeysTest"):
    keypair = keys.Keypair.create_from_uri(uri)
    path = os.path.join(root, wallet, "hotkeys", hotkey)
    Keyfile(path, hotkey).set_keypair(keypair, encrypt=False, overwrite=True)
    return keypair


def test_a_wallet_written_on_disk_is_loaded_from_the_config(tmp_path):
    keypair = _write_hotkey(str(tmp_path))
    config = SimpleNamespace(wallet=SimpleNamespace(name="w", hotkey="h", path=str(tmp_path)))
    wallet = keys.wallet_from_config(config)
    assert wallet.hotkey.ss58_address == keypair.ss58_address
    signature = wallet.hotkey.sign(b"payload")
    assert keys.Keypair(ss58_address=keypair.ss58_address).verify(b"payload", signature)


def test_wallet_defaults_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BT_WALLET_PATH", str(tmp_path))
    monkeypatch.setenv("BT_WALLET_NAME", "envname")
    monkeypatch.setenv("BT_WALLET_HOTKEY", "envhot")
    wallet = keys.Wallet()
    assert (wallet.name, wallet.hotkey_str, wallet.path) == ("envname", "envhot", str(tmp_path))


def test_a_missing_hotkey_file_raises(tmp_path):
    wallet = keys.Wallet("nobody", "nothing", str(tmp_path))
    with pytest.raises(Exception):
        wallet.hotkey.ss58_address


@pytest.mark.parametrize("address, valid", [
    ("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY", True),
    ("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQZ", False),
    ("not-an-address", False),
    ("", False),
    (None, False),
])
def test_valid_ss58(address, valid):
    assert keys.valid_ss58(address) is valid


def test_snapshot_signatures_verify_against_the_hotkey():
    keypair = keys.Keypair.create_from_uri("//HeraldKeysSnapshot")
    signed = publish.sign_snapshot({"epoch": 3, "netuid": 69}, keypair)
    unsigned = {k: v for k, v in signed.items() if k != "signature"}
    message = publish.SNAPSHOT_DOMAIN + publish.canonical_bytes(unsigned)
    assert keys.Keypair(ss58_address=keypair.ss58_address).verify(
        message, bytes.fromhex(signed["signature"][2:]))
