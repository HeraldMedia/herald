"""Score miners' claims into per-UID USD, resolving attribution across competing claims."""

from typing import Callable, Dict, List

from .attribution import Candidate, resolve_attribution
from .fetch import fetch as default_fetch
from .oracle import evaluate_article


def score_claims(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    commit_index,
    hotkey_by_uid: Dict[int, str],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
) -> Dict[int, float]:
    briefs_by_id = {b["id"]: b for b in briefs}
    candidates: List[Candidate] = []

    for uid, claims in claims_by_uid.items():
        hotkey = hotkey_by_uid.get(uid, "")
        onchain = commitments.get(hotkey, "")
        for claim in claims:
            brief = briefs_by_id.get(claim.brief_id)
            if brief is None:
                continue
            result = evaluate_article(claim, onchain, registry, brief, fetch_fn, search_fn)
            commit_epoch = commit_index.commit_epoch(hotkey, onchain) if result.passed else None
            candidates.append(Candidate(
                uid=uid,
                article_id=result.article_id,
                outlet_id=result.evidence.get("outlet_id", ""),
                brief_id=claim.brief_id,
                commit_epoch=commit_epoch,
                usd=result.usd,
                passed=result.passed,
            ))

    usd_by_uid = {uid: 0.0 for uid in claims_by_uid}
    usd_by_uid.update(resolve_attribution(candidates))
    return usd_by_uid
