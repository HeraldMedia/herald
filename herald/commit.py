"""Salted commitment hashing, shared by the miner (build) and validator (verify).

The on-chain value hides the target outlet until the miner reveals the fields at claim time.
"""

import hashlib

_PREFIX = "HRLD1"


def _canonical(fields) -> bytes:
    # Length-prefix each field so no field value can shift another field's boundary.
    return b"".join(f"{len(f)}:{f}|".encode("utf-8") for f in fields)


def commit_hash(
    brief_id: str,
    target_outlet_id: str,
    claimer_hotkey: str,
    nonce: str,
    bond_atto: int,
    version_id: int,
    pre_hash: str = "",
) -> str:
    # pre_hash (the attribution-evidence hash) is appended ONLY when present, so an evidence-less
    # commit hashes byte-identically to the original format and old commitments keep verifying.
    fields = [brief_id, target_outlet_id, claimer_hotkey, nonce, str(bond_atto), str(version_id)]
    if pre_hash:
        fields.append(pre_hash)
    return hashlib.blake2b(_canonical(fields), digest_size=24).hexdigest()


def encode(hash_hex: str) -> str:
    return f"{_PREFIX}|{hash_hex}"


def matches(onchain_value: str, **fields) -> bool:
    return onchain_value == encode(commit_hash(**fields))
