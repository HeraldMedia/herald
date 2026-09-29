"""Signed submission rows for tests, from fixed sr25519 development keys."""

from functools import lru_cache

from bittensor_wallet import Keypair

from herald.validator.news.signatures import draft_digest, submission_message

# A well-formed signature that verifies for nothing: for rows that cannot be signed (a bad field).
UNSIGNABLE = "0x" + "00" * 64


@lru_cache(maxsize=None)
def keypair(name: str) -> Keypair:
    """The development keypair //Herald<name>."""
    return Keypair.create_from_uri(f"//Herald{name}")


def address(name: str) -> str:
    return keypair(name).ss58_address


@lru_cache(maxsize=None)
def _sign(coldkey: str, message: bytes) -> str:
    # sr25519 signatures are randomized: sign each message once so equal rows stay equal.
    return "0x" + bytes(keypair(coldkey).sign(message)).hex()


def signature_for(row: dict, coldkey: str, wrapped: bool = False) -> str:
    """0x-hex signature by keypair `coldkey` over `row`'s submission message."""
    message = submission_message(row["network"], row["netuid"], row["brief_id"],
                                 draft_digest(row["draft_text"]), row["hotkey"])
    if wrapped:
        message = b"<Bytes>" + message + b"</Bytes>"
    return _sign(coldkey, message)


def sign_row(row: dict, miner: str = "A", *, coldkey: str = None, wrapped: bool = False) -> dict:
    """`row` with miner `miner`'s hotkey and coldkey addresses and the coldkey's signature.

    The hotkey is //Herald<miner>Hot and the coldkey //Herald<miner>Cold; `coldkey` names another
    keypair to sign with (the row still states the miner's coldkey). A row whose fields cannot form a
    message gets UNSIGNABLE.
    """
    signed = dict(row)
    signed.setdefault("hotkey", address(miner + "Hot"))
    signed.setdefault("coldkey", address(miner + "Cold"))
    try:
        signed["signature"] = signature_for(signed, coldkey or miner + "Cold", wrapped)
    except (KeyError, TypeError, AttributeError, ValueError):
        signed["signature"] = UNSIGNABLE
    return signed
