"""Slashing modeled as a weight penalty: a slashed hotkey earns nothing until cooldown ends."""

from typing import Dict


class SlashLedger:
    def __init__(self, until: Dict[str, int] = None):
        self._until: Dict[str, int] = dict(until or {})

    def slash(self, hotkey: str, until_epoch: int):
        self._until[hotkey] = max(self._until.get(hotkey, 0), until_epoch)

    def is_slashed(self, hotkey: str, epoch: int) -> bool:
        return epoch < self._until.get(hotkey, 0)

    def to_dict(self) -> dict:
        return {"until": self._until}

    @classmethod
    def from_dict(cls, data: dict) -> "SlashLedger":
        return cls(until=data.get("until", {}))
