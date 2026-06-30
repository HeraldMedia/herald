"""Score miners' claims into winners (for vesting) and per-UID USD."""

from typing import Callable, Dict, List

from .attribution import Candidate, resolve_attribution, winning_candidates
from .bonds import bond_ok
from .fetch import fetch as default_fetch
from .oracle import evaluate_article


def _build_candidates(
    claims_by_uid, commitments, commit_index, hotkey_by_uid,
    alpha_stake_by_uid, briefs, registry, fetch_fn, search_fn, judge_fn,
) -> List[Candidate]:
    briefs_by_id = {b["id"]: b for b in briefs}
    candidates: List[Candidate] = []

    for uid, claims in claims_by_uid.items():
        hotkey = hotkey_by_uid.get(uid, "")
        onchain = commitments.get(hotkey, "")
        relevant = [c for c in claims if c.brief_id in briefs_by_id]
        bonded = bond_ok(
            alpha_stake_by_uid.get(uid, 0.0),
            sum(c.bond_atto for c in relevant),
        )
        for claim in relevant:
            result = evaluate_article(
                claim, onchain, registry, briefs_by_id[claim.brief_id],
                fetch_fn, search_fn, judge_fn,
            )
            passed = result.passed and bonded
            commit_epoch = commit_index.commit_epoch(hotkey, onchain) if passed else None
            candidates.append(Candidate(
                uid=uid,
                article_id=result.article_id,
                outlet_id=result.evidence.get("outlet_id", ""),
                brief_id=claim.brief_id,
                commit_epoch=commit_epoch,
                usd=result.usd,
                passed=passed,
                url=claim.article_url,
                hotkey=hotkey,
            ))
    return candidates


def winning_articles(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    commit_index,
    hotkey_by_uid: Dict[int, str],
    alpha_stake_by_uid: Dict[int, float],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
    judge_fn: Callable = None,
) -> List[Candidate]:
    return winning_candidates(_build_candidates(
        claims_by_uid, commitments, commit_index, hotkey_by_uid,
        alpha_stake_by_uid, briefs, registry, fetch_fn, search_fn, judge_fn,
    ))


def score_claims(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    commit_index,
    hotkey_by_uid: Dict[int, str],
    alpha_stake_by_uid: Dict[int, float],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
    judge_fn: Callable = None,
) -> Dict[int, float]:
    candidates = _build_candidates(
        claims_by_uid, commitments, commit_index, hotkey_by_uid,
        alpha_stake_by_uid, briefs, registry, fetch_fn, search_fn, judge_fn,
    )
    usd_by_uid = {uid: 0.0 for uid in claims_by_uid}
    usd_by_uid.update(resolve_attribution(candidates))
    return usd_by_uid
