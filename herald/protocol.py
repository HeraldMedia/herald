import typing

import bittensor as bt
from pydantic import BaseModel, Field, StringConstraints

# A miner controls its entire ClaimSynapse response, so bound every collection/number: the
# validator parses the whole body before its per-miner slice, and an unbounded list (or a
# huge merkle_path / big-int) would let one miner exhaust its memory.
# Above the per-miner scoring slice (200) but tight enough that a full list of max-size text
# fields can't balloon one response into hundreds of MB before the per-miner slice applies.
MAX_CLAIMS_PER_RESPONSE = 1000
MAX_MERKLE_DEPTH = 64
MAX_BOND_ATTO = 10 ** 30  # legacy compatibility field; bound its big-int digit count
MAX_VERSION_ID = 10 ** 9
MAX_EVIDENCE_TEXT = 20_000  # mirrors herald.evidence.MAX_TEXT_CHARS
MAX_SNAPSHOT_TEXT = 30_000  # extracted article text the miner snapshots at claim time

_ShortStr = typing.Annotated[str, StringConstraints(max_length=128)]


class ClaimRecord(BaseModel):
    """A miner's reveal linking a published article to a prior on-chain commitment."""

    brief_id: str = Field(max_length=128)
    target_outlet_id: str = Field(max_length=128)
    article_url: str = Field(max_length=2048)
    claimer_hotkey: str = Field(max_length=64)
    nonce: str = Field(max_length=128)
    bond_atto: int = Field(ge=0, le=MAX_BOND_ATTO)
    version_id: int = Field(ge=0, le=MAX_VERSION_ID)
    merkle_path: typing.Optional[typing.List[_ShortStr]] = Field(default=None, max_length=MAX_MERKLE_DEPTH)
    claim_sig: typing.Optional[str] = Field(default=None, max_length=256)
    # Attribution evidence (see herald/evidence.py): hashed into the commitment as pre_hash,
    # revealed here and graded by the oracle.
    pre_hash: typing.Optional[str] = Field(default=None, max_length=64)
    evidence_text: typing.Optional[str] = Field(default=None, max_length=MAX_EVIDENCE_TEXT)
    evidence_author: typing.Optional[str] = Field(default=None, max_length=120)
    evidence_window: typing.Optional[typing.List[_ShortStr]] = Field(default=None, max_length=2)
    # Claim-time snapshot of the article's extracted text. Validators anchor it against their
    # own fetch, then run the content checks on these identical bytes so the fleet agrees.
    snapshot_text: typing.Optional[str] = Field(default=None, max_length=MAX_SNAPSHOT_TEXT)


class ClaimSynapse(bt.Synapse):
    """Validator -> miner pull. The miner fills `claims` with its active reveals."""

    request_brief_ids: typing.Optional[typing.List[_ShortStr]] = Field(default=None, max_length=1000)
    claims: typing.Optional[typing.List[ClaimRecord]] = Field(default=None, max_length=MAX_CLAIMS_PER_RESPONSE)
