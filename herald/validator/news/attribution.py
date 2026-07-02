"""Attribution: decide which claim (if any) gets paid per article — strongest evidence first
(a text-proof beats a bare prediction, however early), then earliest commit, then lowest uid."""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Candidate:
    uid: int
    article_id: str
    outlet_id: str
    brief_id: str
    commit_epoch: Optional[int]
    usd: float
    passed: bool
    url: str = ""
    hotkey: str = ""
    level: int = 0  # attribution-evidence level (2 text proof / 1 insider detail / 0 bare)
    claim: object = None  # the originating claim (reveal fields ride into the publish payload)


def _best(cands: List[Candidate]) -> Candidate:
    return min(cands, key=lambda c: (-c.level, c.commit_epoch, c.uid))


def winning_candidates(candidates: List[Candidate]) -> List[Candidate]:
    # usd > 0 so a worthless (e.g. non-indexed) claim can't occupy a paid (outlet, brief) slot
    eligible = [c for c in candidates if c.passed and c.commit_epoch is not None and c.usd > 0]

    by_article: Dict[str, List[Candidate]] = {}
    for c in eligible:
        by_article.setdefault(c.article_id, []).append(c)
    article_winners = [_best(group) for group in by_article.values()]

    by_placement: Dict[tuple, List[Candidate]] = {}
    for c in article_winners:
        by_placement.setdefault((c.outlet_id, c.brief_id), []).append(c)

    return [_best(group) for group in by_placement.values()]


def resolve_attribution(candidates: List[Candidate]) -> Dict[int, float]:
    usd_by_uid: Dict[int, float] = {c.uid: 0.0 for c in candidates}
    for winner in winning_candidates(candidates):
        usd_by_uid[winner.uid] += winner.usd
    return usd_by_uid
