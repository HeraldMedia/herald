"""Earliest-commit-wins attribution: decide which claim (if any) gets paid per article."""

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


def _earliest(cands: List[Candidate]) -> Candidate:
    return min(cands, key=lambda c: (c.commit_epoch, c.uid))


def resolve_attribution(candidates: List[Candidate]) -> Dict[int, float]:
    usd_by_uid: Dict[int, float] = {c.uid: 0.0 for c in candidates}

    eligible = [c for c in candidates if c.passed and c.commit_epoch is not None]

    # one winner per article (earliest commit), then one paid placement per (outlet, brief)
    by_article: Dict[str, List[Candidate]] = {}
    for c in eligible:
        by_article.setdefault(c.article_id, []).append(c)

    placement_winners = [_earliest(group) for group in by_article.values()]

    by_placement: Dict[tuple, List[Candidate]] = {}
    for c in placement_winners:
        by_placement.setdefault((c.outlet_id, c.brief_id), []).append(c)

    for group in by_placement.values():
        winner = _earliest(group)
        usd_by_uid[winner.uid] += winner.usd

    return usd_by_uid
