"""Read on-chain commitments with their set-block, so attribution is chain-deterministic.

`get_all_commitments` discards the block; the raw CommitmentOf value carries it. We read it
directly so every validator derives the same commit ordering regardless of when it observed.
"""

from bittensor.core.chain_data.utils import decode_account_id, decode_metadata


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
            hotkey = decode_account_id(id_[0])
            result[hotkey] = (decode_metadata(value), _block_of(value))
        except Exception:
            continue
    return result
