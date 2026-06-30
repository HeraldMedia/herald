"""Per-article vesting: release the reward in installments while the article stays online."""

from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

VESTING = "VESTING"
COMPLETED = "COMPLETED"
CLAWBACK = "CLAWBACK"


@dataclass
class VestEntry:
    uid: int
    total_usd: float
    installment_usd: float
    remaining: int
    status: str


class VestingLedger:
    def __init__(self, vest_epochs: int, entries: Dict[str, dict] = None):
        self.vest_epochs = vest_epochs
        self._entries: Dict[str, VestEntry] = {
            k: VestEntry(**v) for k, v in (entries or {}).items()
        }

    def start(self, article_id: str, uid: int, total_usd: float):
        if article_id in self._entries:
            return
        self._entries[article_id] = VestEntry(
            uid=uid,
            total_usd=total_usd,
            installment_usd=total_usd / self.vest_epochs,
            remaining=self.vest_epochs,
            status=VESTING,
        )

    def release(self, article_id: str, alive: bool) -> Tuple[float, bool]:
        entry = self._entries.get(article_id)
        if entry is None or entry.status != VESTING:
            return 0.0, False
        if not alive:
            entry.status = CLAWBACK
            return 0.0, True
        entry.remaining -= 1
        if entry.remaining <= 0:
            entry.status = COMPLETED
        return entry.installment_usd, False

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
