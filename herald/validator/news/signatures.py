"""The miner's signature that binds a submission to its hotkey.

A contributor's wallet signs, with the coldkey that owns their hotkey, the message below at draft
upload. Validators rebuild it from the feed row, using their own digest of the draft text, so the
backend can neither move a submission to another hotkey nor change the draft it vouches for:

    Herald submission v1
    network: <network>
    netuid: <netuid>
    brief: <brief_id>
    draft: <sha256 hex of the draft text, UTF-8>
    hotkey: <hotkey SS58>

Lines are joined by "\\n" with no trailing newline. Wallets sign raw messages either as they are or
wrapped in ``<Bytes>…</Bytes>``; both verify. Whether the coldkey owns the hotkey is read from the
chain at the scoring block (SubtensorModule.Owner), not taken from the row.
"""

import hashlib
import re

MESSAGE_HEADER = "Herald submission v1"
_SIGNATURE = re.compile(r"0x[0-9a-fA-F]{128}")


def draft_digest(draft_text: str) -> str:
    """Lowercase sha256 hex of the draft text encoded as UTF-8."""
    return hashlib.sha256(draft_text.encode("utf-8")).hexdigest()


def submission_message(network: str, netuid: int, brief_id: str, draft_sha256: str,
                       hotkey: str) -> bytes:
    """The exact bytes a contributor's coldkey signs for one submission."""
    lines = (
        MESSAGE_HEADER,
        f"network: {network}",
        f"netuid: {int(netuid)}",
        f"brief: {brief_id}",
        f"draft: {draft_sha256}",
        f"hotkey: {hotkey}",
    )
    return "\n".join(lines).encode("utf-8")


def valid_ss58(address) -> bool:
    """True for a string that decodes as an SS58 account address."""
    if not isinstance(address, str) or not 40 <= len(address) <= 64:
        return False
    from bittensor_wallet import Keypair
    try:
        Keypair(ss58_address=address)
    except Exception:
        return False
    return True


def valid_signature_format(signature) -> bool:
    """A 0x-prefixed hex string of 64 bytes."""
    return isinstance(signature, str) and _SIGNATURE.fullmatch(signature) is not None


def verify_submission(row: dict) -> bool:
    """True when the row's coldkey signed this row's submission message for its hotkey.

    The row needs network, netuid, brief_id, draft_text, hotkey, coldkey and signature. A malformed
    address or signature does not verify.
    """
    coldkey, hotkey, signature = row.get("coldkey"), row.get("hotkey"), row.get("signature")
    if not (valid_ss58(coldkey) and valid_ss58(hotkey) and valid_signature_format(signature)):
        return False
    message = submission_message(row["network"], row["netuid"], row["brief_id"],
                                 draft_digest(row["draft_text"]), hotkey)
    from bittensor_wallet import Keypair
    try:
        return bool(Keypair(ss58_address=coldkey).verify(message, signature))
    except Exception:
        return False
