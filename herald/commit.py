"""Salted commitment hashing, shared by the miner (build) and validator (verify).

The on-chain value hides the target outlet until the miner reveals the fields at claim time.
"""

import hashlib

_PREFIX = "HRLD1"


def commit_hash(
    brief_id: str,
    target_outlet_id: str,
    claimer_hotkey: str,
    nonce: str,
    bond_atto: int,
    version_id: int,
) -> str:
    payload = "\x1f".join(
        [brief_id, target_outlet_id, claimer_hotkey, nonce, str(bond_atto), str(version_id)]
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=24).hexdigest()


def encode(hash_hex: str) -> str:
    return f"{_PREFIX}|{hash_hex}"


def matches(onchain_value: str, **fields) -> bool:
    return onchain_value == encode(commit_hash(**fields))
