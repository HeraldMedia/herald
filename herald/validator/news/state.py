"""Persistent Herald validator state: commit index, vesting, and slash ledgers."""

import json
import os

import bittensor as bt

from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS
from .commit_index import CommitIndex
from .slashing import SlashLedger
from .vesting import VestingLedger


class HeraldState:
    def __init__(self, commit_index: CommitIndex, vesting: VestingLedger, slash: SlashLedger):
        self.commit_index = commit_index
        self.vesting = vesting
        self.slash = slash

    @classmethod
    def fresh(cls) -> "HeraldState":
        return cls(CommitIndex(EPOCH_LEN), VestingLedger(VEST_EPOCHS), SlashLedger())

    def to_dict(self) -> dict:
        return {
            "commit_index": self.commit_index.to_dict(),
            "vesting": self.vesting.to_dict(),
            "slash": self.slash.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HeraldState":
        return cls(
            CommitIndex.from_dict(data["commit_index"]),
            VestingLedger.from_dict(data["vesting"]),
            SlashLedger.from_dict(data["slash"]),
        )

    def save(self, path: str):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f)
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
