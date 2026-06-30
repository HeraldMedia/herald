import typing

import bittensor as bt
from pydantic import BaseModel, Field


class ClaimRecord(BaseModel):
    """A miner's reveal linking a published article to a prior on-chain commitment."""

    brief_id: str = Field(max_length=128)
    target_outlet_id: str = Field(max_length=128)
    article_url: str = Field(max_length=2048)
    claimer_hotkey: str = Field(max_length=64)
    nonce: str = Field(max_length=128)
    bond_atto: int = Field(ge=0)
    version_id: int = Field(ge=0)
    merkle_path: typing.Optional[typing.List[str]] = None
    claim_sig: typing.Optional[str] = Field(default=None, max_length=256)


class ClaimSynapse(bt.Synapse):
    """Validator -> miner pull. The miner fills `claims` with its active reveals."""

    request_brief_ids: typing.Optional[typing.List[str]] = None
    claims: typing.Optional[typing.List[ClaimRecord]] = None
