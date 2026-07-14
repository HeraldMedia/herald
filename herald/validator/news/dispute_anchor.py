"""On-chain dispute anchor: a staker flags a specific placement for escalated re-scrutiny.

A disputer commits ``HRLDDIS|<article_id_hash>`` from their hotkey. Validators read it on chain
(the same path as miner commitments), so every validator sees the same disputes at the same epoch
(commit block // VEST_EPOCH_LEN) and resolves them identically. The article hash keeps the committed
value bounded and is taken over the canonical article_id the oracle already uses (the canonical URL).

A dispute only *triggers* the validator's own escalated re-check (mandatory pinned judge); the
existing oracle/persistence verdict decides upheld vs rejected. See dev/DISPUTE_DESIGN.md.
"""

import hashlib
from typing import Optional

_PREFIX = "HRLDDIS"
_HASH_BYTES = 24  # blake2b-192 -> 48 hex chars, same digest size as the registry anchor


def article_id_hash(article_id: str) -> str:
    return hashlib.blake2b(article_id.encode("utf-8"), digest_size=_HASH_BYTES).hexdigest()


def encode_dispute(article_id: str) -> str:
    return f"{_PREFIX}|{article_id_hash(article_id)}"


def parse_dispute(value) -> Optional[str]:
    """Return the article_id_hash carried by a dispute commitment value, else None."""
    if not isinstance(value, str) or not value.startswith(_PREFIX + "|"):
        return None
    parts = value.split("|")
    if len(parts) != 2 or len(parts[1]) != _HASH_BYTES * 2:
        return None
    try:
        int(parts[1], 16)
    except ValueError:
        return None  # not hex -> not a valid anchor
    return parts[1]


def matches(article_id: str, dispute_value) -> bool:
    """True iff `dispute_value` is a dispute commitment for `article_id`."""
    h = parse_dispute(dispute_value)
    return h is not None and h == article_id_hash(article_id)
