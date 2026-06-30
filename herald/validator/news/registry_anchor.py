"""On-chain anchor binding a registry version to its content hash.

The authority commits `HRLDREG|<version>|<hash>|<effective_block>` on chain; validators
confirm the signed file they loaded matches that binding, so all validators score against
the identical list edition and a forged older edition cannot be substituted.
"""

import hashlib
from typing import Optional

from .registry_signing import canonical_bytes

_PREFIX = "HRLDREG"


def content_hash(data: dict) -> str:
    return hashlib.blake2b(canonical_bytes(data), digest_size=24).hexdigest()


def encode_anchor(version_id: int, content_hash_hex: str, effective_block: int) -> str:
    return f"{_PREFIX}|{version_id}|{content_hash_hex}|{effective_block}"


def parse_anchor(anchor_value: str) -> Optional[dict]:
    parts = anchor_value.split("|")
    if len(parts) != 4 or parts[0] != _PREFIX:
        return None
    try:
        return {"version_id": int(parts[1]), "hash": parts[2], "effective_block": int(parts[3])}
    except ValueError:
        return None


def verify_anchor(data: dict, anchor_value: str) -> bool:
    parsed = parse_anchor(anchor_value)
    if parsed is None:
        return False
    return parsed["version_id"] == data.get("version_id") and parsed["hash"] == content_hash(data)
