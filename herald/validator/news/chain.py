"""Read on-chain commitments with their set-block, so attribution is chain-deterministic.

`get_all_commitments` discards the block; the raw CommitmentOf value carries it. We read it
directly so every validator derives the same commit ordering regardless of when it observed.
"""

def decode_metadata(metadata) -> str:
    """The text of a Commitments.CommitmentOf value: its first field's bytes as UTF-8.

    The value is ``{"deposit", "block", "info": {"fields": [{"Raw<n>": "0x..."}, ...]}}``. Only the
    first field is read; a field without a payload (a unit variant such as ``"None"``) reads as "",
    and bytes that are not valid UTF-8 are dropped.
    """
    commitment = metadata["info"]["fields"][0]
    if isinstance(commitment, str):
        return ""
    payload = next(iter(commitment.values()))
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="ignore")
    return bytes.fromhex(payload.removeprefix("0x")).decode("utf-8", errors="ignore")


def _block_of(value) -> int:
    raw = value.value if hasattr(value, "value") else value
    return int(raw["block"])


def get_commitments_with_block(subtensor, netuid: int, block=None) -> dict:
    """Return {hotkey: (commitment_value, set_block)} for every committed hotkey."""
    result = {}
    query = subtensor.query_map(
        module="Commitments", name="CommitmentOf", params=[netuid], block=block
    )
    for id_, value in query:
        try:
            # Map keys come back ss58-decoded (strings); a (hotkey,) tuple is also accepted.
            hotkey = id_ if isinstance(id_, str) else id_[0]
            result[hotkey] = (decode_metadata(value), _block_of(value))
        except Exception:
            continue
    return result


def get_hotkey_owners(subtensor, hotkeys, block=None) -> dict:
    """{hotkey: the coldkey that owns it at `block`, or None when the chain records no owner}.

    Read from SubtensorModule.Owner, which the chain sets to the coldkey that first registers or
    associates a hotkey and which only that coldkey can change. One read per distinct hotkey. A read
    that fails raises, so an epoch is never scored on a guessed owner.
    """
    return {hotkey: subtensor.get_hotkey_owner(hotkey, block=block) for hotkey in sorted(set(hotkeys))}
