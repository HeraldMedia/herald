"""Persistent Herald validator state: commit index, vesting, and slash ledgers."""

import json
import os

import bittensor as bt

from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS
from .commit_index import CommitIndex
from .disputes import DisputeLedger
from .slashing import SlashLedger
from .vesting import VestingLedger


def _json_np_safe(o):
    """json.dump default: unbox numpy scalars (they carry a .item()) to native Python."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


class HeraldState:
    def __init__(self, commit_index: CommitIndex, vesting: VestingLedger, slash: SlashLedger,
                 disputes: DisputeLedger = None, pool_spent: dict = None,
                 last_scored_epoch: int = -1, last_weight_epoch: int = -1):
        self.commit_index = commit_index
        self.vesting = vesting
        self.slash = slash
        self.disputes = disputes if disputes is not None else DisputeLedger()
        # {brief_id: cumulative USD drawn from that client brief's reward pool}, so a pool is never
        # over-paid across epochs. Standing briefs pay from emissions and never appear here.
        self.pool_spent = dict(pool_spent or {})
        # Persisted so a restart inside an already-scored epoch doesn't re-score it: the vesting
        # ledger already released that epoch's installments, so a re-run would lose that day's vector.
        self.last_scored_epoch = last_scored_epoch
        # Chain weight publication is independently checkpointed. Bittensor's short weight-update
        # interval must not cause an unchanged Herald daily allocation to be submitted repeatedly.
        self.last_weight_epoch = last_weight_epoch

    @classmethod
    def fresh(cls) -> "HeraldState":
        return cls(CommitIndex(EPOCH_LEN), VestingLedger(VEST_EPOCHS), SlashLedger(), DisputeLedger())

    def to_dict(self) -> dict:
        return {
            "commit_index": self.commit_index.to_dict(),
            "vesting": self.vesting.to_dict(),
            "slash": self.slash.to_dict(),
            "disputes": self.disputes.to_dict(),
            "pool_spent": self.pool_spent,
            "last_scored_epoch": self.last_scored_epoch,
            "last_weight_epoch": self.last_weight_epoch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HeraldState":
        # epoch_len / vest_epochs are consensus parameters: take them from config, never the
        # persisted file. A divisor that drifted across an upgrade would otherwise diverge
        # winner selection (commit_epoch) and installment size between validators.
        ci = data.get("commit_index", {})
        ve = data.get("vesting", {})
        return cls(
            CommitIndex(EPOCH_LEN, ci.get("first_seen", {})),
            VestingLedger(VEST_EPOCHS, ve.get("entries", {})),
            SlashLedger.from_dict(data["slash"]),
            DisputeLedger.from_dict(data.get("disputes", {})),
            data.get("pool_spent", {}),
            data.get("last_scored_epoch", -1),
            data.get("last_weight_epoch", -1),
        )

    def save(self, path: str):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            # bittensor 10.x hands numpy scalars (int64 uids, float32 stakes) into the domain
            # objects; unbox them so persistence never dies on "int64 is not JSON serializable".
            json.dump(self.to_dict(), f, default=_json_np_safe)
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written state file

    @classmethod
    def load(cls, path: str) -> "HeraldState":
        if not os.path.exists(path):
            return cls.fresh()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception as e:
            bt.logging.error(f"Corrupt Herald state at {path} ({e}); starting fresh")
            return cls.fresh()
