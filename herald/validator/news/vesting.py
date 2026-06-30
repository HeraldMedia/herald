"""Per-article vesting: release the reward in installments while the article stays valuable."""

from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

VESTING = "VESTING"
COMPLETED = "COMPLETED"
CLAWBACK = "CLAWBACK"
EXPIRED = "EXPIRED"


@dataclass
class VestEntry:
    uid: int
    total_usd: float
    installment_usd: float
    remaining: int
    status: str
    url: str = ""
    hotkey: str = ""
    brief_id: str = ""
    commit_epoch: int = 0
    last_release_epoch: int = -1
    start_epoch: int = 0
    dead_streak: int = 0
    last_dead_epoch: int = -1


class VestingLedger:
    def __init__(self, vest_epochs: int, entries: Dict[str, dict] = None):
        self.vest_epochs = vest_epochs
        self._entries: Dict[str, VestEntry] = {
            k: VestEntry(**v) for k, v in (entries or {}).items()
        }

    def start(self, article_id, uid, total_usd, url="", hotkey="", brief_id="",
              commit_epoch=0, start_epoch=0):
        existing = self._entries.get(article_id)
        if existing is not None:
            # earliest commit wins even if it reveals in a later cycle
            if existing.status == VESTING and commit_epoch < existing.commit_epoch:
                existing.uid = uid
                existing.hotkey = hotkey
                existing.commit_epoch = commit_epoch
                existing.brief_id = brief_id
                existing.total_usd = total_usd
                existing.installment_usd = total_usd / self.vest_epochs
            return
        self._entries[article_id] = VestEntry(
            uid=uid,
            total_usd=total_usd,
            installment_usd=total_usd / self.vest_epochs,
            remaining=self.vest_epochs,
            status=VESTING,
            url=url,
            hotkey=hotkey,
            brief_id=brief_id,
            commit_epoch=commit_epoch,
            start_epoch=start_epoch,
        )

    def release(self, article_id: str, epoch: int) -> float:
        """Release one installment if the article is VESTING and not already released this epoch."""
        entry = self._entries.get(article_id)
        if entry is None or entry.status != VESTING:
            return 0.0
        if epoch <= entry.last_release_epoch:
            return 0.0
        entry.last_release_epoch = epoch
        entry.remaining -= 1
        if entry.remaining <= 0:
            entry.status = COMPLETED
        return entry.installment_usd

    def clawback(self, article_id: str) -> bool:
        """Mark a VESTING article as clawed back (article confirmed removed or gone paid)."""
        entry = self._entries.get(article_id)
        if entry is None or entry.status != VESTING:
            return False
        entry.status = CLAWBACK
        return True

    def expire(self, article_id: str) -> bool:
        """Terminate a long-held VESTING article (no further pay/clawback)."""
        entry = self._entries.get(article_id)
        if entry is None or entry.status != VESTING:
            return False
        entry.status = EXPIRED
        return True

    def entry(self, article_id: str) -> VestEntry:
        return self._entries[article_id]

    def status(self, article_id: str) -> str:
        return self._entries[article_id].status

    def active_article_ids(self) -> List[str]:
        return [aid for aid, e in self._entries.items() if e.status == VESTING]

    def to_dict(self) -> dict:
        return {"vest_epochs": self.vest_epochs,
                "entries": {k: asdict(v) for k, v in self._entries.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "VestingLedger":
        return cls(vest_epochs=data["vest_epochs"], entries=data.get("entries", {}))
