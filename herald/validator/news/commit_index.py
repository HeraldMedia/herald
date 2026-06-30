"""Chain-derived index of when each commitment first appeared, for deterministic ordering."""

from typing import Dict, Optional


class CommitIndex:
    def __init__(self, epoch_len: int, first_seen: Dict[str, int] = None):
        self.epoch_len = epoch_len
        # key "hotkey\x1fvalue" -> first block observed
        self._first_seen: Dict[str, int] = dict(first_seen or {})

    @staticmethod
    def _key(hotkey: str, value: str) -> str:
        return f"{len(hotkey)}:{hotkey}|{value}"

    def observe(self, commitments_with_block: Dict[str, tuple]):
        """commitments_with_block: {hotkey: (value, on_chain_block)}."""
        for hotkey, (value, block) in commitments_with_block.items():
            key = self._key(hotkey, value)
            prior = self._first_seen.get(key)
            if prior is None or block < prior:
                self._first_seen[key] = block

    def first_seen_block(self, hotkey: str, value: str) -> Optional[int]:
        return self._first_seen.get(self._key(hotkey, value))

    def commit_epoch(self, hotkey: str, value: str) -> Optional[int]:
        block = self.first_seen_block(hotkey, value)
        return None if block is None else block // self.epoch_len

    def to_dict(self) -> dict:
        return {"epoch_len": self.epoch_len, "first_seen": self._first_seen}

    @classmethod
    def from_dict(cls, data: dict) -> "CommitIndex":
        return cls(epoch_len=data["epoch_len"], first_seen=data.get("first_seen", {}))
