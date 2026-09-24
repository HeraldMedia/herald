"""Dispute ledger.

Validator state keeps persisting this ledger so existing state files stay readable. Scoring does not
open or resolve disputes.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from .persisted import rebuild_records

OPEN = "open"
UPHELD = "upheld"
REJECTED = "rejected"


@dataclass
class Dispute:
    article_id: str
    disputer_hotkey: str
    filed_epoch: int
    status: str = OPEN


class DisputeLedger:
    def __init__(self, disputes: Dict[str, dict] = None):
        # Same rollback rule as the vesting ledger: unknown persisted keys are dropped, not fatal.
        self._d: Dict[str, Dispute] = rebuild_records(Dispute, disputes, label="dispute")

    def open(self, article_id: str, disputer_hotkey: str, filed_epoch: int) -> bool:
        """Register a dispute. One per article; idempotent on re-read, so callers must register in
        ascending (block, hotkey) order for the earliest filer to win. Returns True if newly opened.
        """
        if article_id in self._d:
            return False
        self._d[article_id] = Dispute(article_id, disputer_hotkey, filed_epoch)
        return True

    def active(self, article_id: str) -> Optional[Dispute]:
        d = self._d.get(article_id)
        return d if d is not None and d.status == OPEN else None

    def is_disputed(self, article_id: str) -> bool:
        return self.active(article_id) is not None

    def resolve(self, article_id: str, upheld: bool) -> Optional[Dispute]:
        d = self.active(article_id)
        if d is None:
            return None
        d.status = UPHELD if upheld else REJECTED
        return d

    def open_disputes(self) -> List[Dispute]:
        return [d for d in self._d.values() if d.status == OPEN]

    def to_dict(self) -> dict:
        return {k: vars(d) for k, d in self._d.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "DisputeLedger":
        return cls(disputes=data or {})
