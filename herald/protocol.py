import typing

import bittensor as bt
from pydantic import BaseModel


class ClaimRecord(BaseModel):
    """A miner's reveal linking a published article to a prior on-chain commitment."""

    brief_id: str
    target_outlet_id: str
    article_url: str
    claimer_hotkey: str
    nonce: str
    bond_atto: int
    version_id: int
    merkle_path: typing.Optional[typing.List[str]] = None
    claim_sig: typing.Optional[str] = None


class ClaimSynapse(bt.Synapse):
    """Validator -> miner pull. The miner fills `claims` with its active reveals."""

    request_brief_ids: typing.Optional[typing.List[str]] = None
    claims: typing.Optional[typing.List[ClaimRecord]] = None
