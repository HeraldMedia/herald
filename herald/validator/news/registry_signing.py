"""Ed25519 signing/verification of the outlet registry (its trust anchor)."""

import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_bytes(data: dict) -> bytes:
    payload = {k: v for k, v in data.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_keypair():
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw().hex(), sk.public_key().public_bytes_raw().hex()


def sign(data: dict, private_key_hex: str) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return sk.sign(canonical_bytes(data)).hex()


def verify(data: dict, public_key_hex: str) -> bool:
    signature = data.get("signature")
    if not signature:
        return False
    pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    try:
        pk.verify(bytes.fromhex(signature), canonical_bytes(data))
        return True
    except (InvalidSignature, ValueError):
        return False
