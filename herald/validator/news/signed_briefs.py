"""Operator signature over the brief payload validators fetch — so a brief's `reward_pool`, `kind`,
and funded state (which direct emissions) are attributable to the operator, not a bare
trust-the-endpoint value.

Mirrors `registry.load_registry`'s verification and reuses `registry_signing`. The signature is the
trusted "funded" signal: the operator confirms the client's treasury payment, then signs the brief
funded with its reward pool. See FUNDING_DESIGN.md.

CAVEAT: briefs are dynamic, so the board signs them with an online key — that gives tamper-evidence
in transit + operator attribution, but a board compromise could still forge briefs. An on-chain brief
anchor (like the outlet registry's) is the future hardening that closes that gap.
"""

import os
import time


def verify_briefs(payload: dict) -> dict:
    """Verify the operator signature on a fetched brief payload. Returns it when valid (or when
    unsigned mode is allowed); raises ValueError on a bad — or required-but-missing — signature.

    Also enforces freshness: a validly-signed payload could otherwise be replayed forever (e.g. a
    defunded brief re-served as funded by a MITM/cache). The board puts `signed_at` (unix seconds)
    inside the signed payload; reject it when older than HERALD_BRIEFS_MAX_AGE (0 disables).
    """
    pubkey = os.getenv("HERALD_BRIEFS_PUBKEY")
    if pubkey:
        from .registry_signing import verify
        if not verify(payload, pubkey):
            raise ValueError("brief payload signature verification failed")
        max_age = int(os.getenv("HERALD_BRIEFS_MAX_AGE", "900"))
        if max_age > 0:
            signed_at = payload.get("signed_at")
            if not isinstance(signed_at, (int, float)) or abs(time.time() - signed_at) > max_age:
                raise ValueError("brief payload stale or missing signed_at (replay protection)")
    elif os.getenv("HERALD_REQUIRE_SIGNED_BRIEFS", "false").lower() == "true":
        raise ValueError("HERALD_BRIEFS_PUBKEY required but not set")
    return payload


def sign_briefs(payload: dict, private_key_hex: str) -> dict:
    """Attach the operator signature to a brief payload (used by the brief board when it serves the
    validator feed). Signs the canonical bytes of everything except the signature field itself.
    """
    from .registry_signing import sign
    return {**payload, "signature": sign(payload, private_key_hex)}
