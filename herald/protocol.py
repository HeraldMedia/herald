"""The claim record a miner revealed against an on-chain commitment.

The axon synapse that carried these records between neurons was removed with the miner neuron; the
record and its bounds remain for code that still parses stored or exported claims.
"""

import typing

from pydantic import BaseModel, Field, StringConstraints

# A claim body is untrusted input, so bound every collection/number: an unbounded list (or a huge
# merkle_path / big-int) would let one sender exhaust the parser's memory.
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
    # Claim-time snapshot of the article's extracted text. Validators score their own fetch of the
    # article and reject a snapshot that does not match it; publisher-API outlets, which validators
    # fetch only as an excerpt, require one.
    snapshot_text: typing.Optional[str] = Field(default=None, max_length=MAX_SNAPSHOT_TEXT)
