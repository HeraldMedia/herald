"""Operator signature over the brief payload validators fetch — so a brief's `boost` (which directs
emissions) is attributable to the operator, not a bare trust-the-endpoint value.

Mirrors `registry.load_registry`'s verification and reuses `registry_signing`. The brief's effective
boost is independently clamped to [1, HERALD_FUND_BOOST_MAX] in scoring (the binding consensus rail),
so the signature adds accountability + tamper-evidence on top of that ceiling. See FUNDING_DESIGN.md.

CAVEAT: briefs are dynamic, so the board signs them with an online key — that gives tamper-evidence
in transit + operator attribution, but a board compromise could still forge briefs (bounded to 3×).
An on-chain brief anchor (like the outlet registry's) is the future hardening that closes that gap.
"""

import os


def verify_briefs(payload: dict) -> dict:
    """Verify the operator signature on a fetched brief payload. Returns it when valid (or when
    unsigned mode is allowed); raises ValueError on a bad — or required-but-missing — signature.
    """
    pubkey = os.getenv("HERALD_BRIEFS_PUBKEY")
    if pubkey:
        from .registry_signing import verify
        if not verify(payload, pubkey):
            raise ValueError("brief payload signature verification failed")
    elif os.getenv("HERALD_REQUIRE_SIGNED_BRIEFS", "false").lower() == "true":
        raise ValueError("HERALD_BRIEFS_PUBKEY required but not set")
    return payload


def sign_briefs(payload: dict, private_key_hex: str) -> dict:
    """Attach the operator signature to a brief payload (used by the brief board when it serves the
    validator feed). Signs the canonical bytes of everything except the signature field itself.
    """
    from .registry_signing import sign
    return {**payload, "signature": sign(payload, private_key_hex)}
