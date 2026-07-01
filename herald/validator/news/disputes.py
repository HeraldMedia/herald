"""Dispute ledger: tracks active placement disputes and their resolution.

Consensus-safe by construction: a dispute only marks an article for the escalated re-check; the
existing deterministic oracle/persistence path decides upheld (article failed -> clawback+slash the
miner, reward the disputer) vs rejected (still alive at window close -> slash the disputer). One
active dispute per article — the earliest on-chain filer wins (the caller registers in block order).
Incentives are weight-only; see DISPUTE_DESIGN.md.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

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
        self._d: Dict[str, Dispute] = {k: Dispute(**v) for k, v in (disputes or {}).items()}

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


def settle_persistence(article_id, entry, status, epoch, *, vesting, slash, disputes,
                       dead_confirm, cooldown, window, reward_fraction, uid_by_hotkey):
    """Apply one epoch's persistence verdict to a vesting article and any open dispute.

    This is the validator's Pass-1 rule (called from forward.py), factored out as a pure,
    deterministic function so the consensus-critical decision is directly unit-testable without the
    validator runtime. Mutates the vesting / slash / dispute ledgers in place; returns
    ``(installment_usd_or_0.0, {disputer_uid: reward_usd})``.

      dead  -> after `dead_confirm` consecutive confirmed epochs: clawback + slash the miner; if the
               article was disputed, resolve UPHELD and pay the disputer `reward_fraction` of the
               forfeited (otherwise-burned) vesting.
      alive -> release one installment; if a dispute has been open past `window` epochs while the
               article stayed alive, resolve REJECTED and slash the disputer (grief penalty).
      hold  -> withhold pay, no clawback; the dispute stays open.
    """
    disp = disputes.active(article_id)
    if status == "dead":
        if epoch > entry.last_dead_epoch:  # idempotent if this epoch re-runs after a restart
            entry.dead_streak += 1
            entry.last_dead_epoch = epoch
        if entry.dead_streak >= dead_confirm:
            forfeited = entry.installment_usd * entry.remaining  # unreleased, before clawback
            if vesting.clawback(article_id):
                slash.slash(entry.hotkey, epoch + cooldown)
                if disp is not None and disputes.resolve(article_id, True):
                    duid = uid_by_hotkey.get(disp.disputer_hotkey)
                    if duid is not None:
                        return 0.0, {duid: forfeited * reward_fraction}
        return 0.0, {}
    if status == "alive":
        entry.dead_streak = 0
        if disp is not None and epoch - disp.filed_epoch >= window:
            if disputes.resolve(article_id, False):  # stayed alive past the window: grief slash
                slash.slash(disp.disputer_hotkey, epoch + cooldown)
        return vesting.release(article_id, epoch), {}
    return 0.0, {}  # "hold"
