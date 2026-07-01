"""On-chain proof that a client is funding a brief: ``HRLDFUND|<brief_id_hash>`` from the funder's
hotkey (the same Commitments rail as HRLD1 / HRLDDIS; one brief per funding hotkey).

Tier A (operator-signed): the operator verifies the funder's commit + their on-chain α holding, then
signs the brief's boost into the signed brief registry. The hash keeps the committed value bounded
and is byte-exact across validators + the console (blake2b-192 over the canonical brief id).
See FUNDING_DESIGN.md.
"""

import hashlib
from typing import Optional

_PREFIX = "HRLDFUND"
_HASH_BYTES = 24  # blake2b-192 -> 48 hex chars, same digest size as the registry/dispute anchors


def brief_id_hash(brief_id: str) -> str:
    return hashlib.blake2b(brief_id.encode("utf-8"), digest_size=_HASH_BYTES).hexdigest()


def encode_funding(brief_id: str) -> str:
    return f"{_PREFIX}|{brief_id_hash(brief_id)}"


def parse_funding(value) -> Optional[str]:
    """Return the brief_id_hash carried by a funding commitment value, else None."""
    if not isinstance(value, str) or not value.startswith(_PREFIX + "|"):
        return None
    parts = value.split("|")
    if len(parts) != 2 or len(parts[1]) != _HASH_BYTES * 2:
        return None
    try:
        int(parts[1], 16)
    except ValueError:
        return None
    return parts[1]


def matches(brief_id: str, funding_value) -> bool:
    """True iff `funding_value` is a funding commitment for `brief_id`."""
    h = parse_funding(funding_value)
    return h is not None and h == brief_id_hash(brief_id)
