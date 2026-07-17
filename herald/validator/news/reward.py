"""Score miners' claims into winners (for vesting) and per-UID USD."""

from typing import Callable, Dict, List

from herald.validator.utils.config import HERALD_ATTR_MULT, HERALD_ATTR_TEXT_THRESHOLD

from .attribution import Candidate, resolve_attribution, winning_candidates
from .fetch import fetch as default_fetch
from .oracle import evaluate_article
from .textmatch import containment


def _demote_text_collisions(candidates: List[Candidate], texts: Dict[int, str]) -> None:
    """Two hotkeys pre-committing overlapping text (e.g. the client's public press release) proves
    the CAMPAIGN caused the coverage, not either miner — demote every colliding level-2 claim on
    the same article to level 1. Demote-all is order-independent, so validators agree."""
    by_article: Dict[str, List[Candidate]] = {}
    for c in candidates:
        if c.level == 2:
            by_article.setdefault(c.article_id, []).append(c)
    for group in by_article.values():
        if len({c.hotkey for c in group}) < 2:
            continue
        colliding = set()
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.hotkey == b.hotkey:
                    continue
                ta, tb = texts.get(id(a), ""), texts.get(id(b), "")
                if (containment(ta, tb) >= HERALD_ATTR_TEXT_THRESHOLD
                        or containment(tb, ta) >= HERALD_ATTR_TEXT_THRESHOLD):
                    colliding.add(id(a))
                    colliding.add(id(b))
        for c in group:
            if id(c) in colliding:
                c.level = 1
                if HERALD_ATTR_MULT.get(2, 0.0) > 0:
                    c.usd = c.usd / HERALD_ATTR_MULT[2] * HERALD_ATTR_MULT.get(1, 0.0)


def _build_candidates(
    claims_by_uid, commitments, commit_index, hotkey_by_uid,
    briefs, registry, fetch_fn, search_fn, judge_fn,
) -> List[Candidate]:
    briefs_by_id = {b["id"]: b for b in briefs}
    candidates: List[Candidate] = []
    texts: Dict[int, str] = {}  # candidate id() -> revealed evidence text (collision check)

    for uid, claims in claims_by_uid.items():
        hotkey = hotkey_by_uid.get(uid, "")
        onchain = commitments.get(hotkey, "")
        relevant = [c for c in claims if c.brief_id in briefs_by_id]
        for claim in relevant:
            result = evaluate_article(
                claim, onchain, registry, briefs_by_id[claim.brief_id],
                fetch_fn, search_fn, judge_fn, serving_hotkey=hotkey,
            )
            passed = result.passed
            commit_epoch = commit_index.commit_epoch(hotkey, onchain) if passed else None
            candidate = Candidate(
                uid=uid,
                article_id=result.article_id,
                outlet_id=result.evidence.get("outlet_id", ""),
                brief_id=claim.brief_id,
                commit_epoch=commit_epoch,
                usd=result.usd,
                passed=passed,
                url=claim.article_url,
                hotkey=hotkey,
                level=result.evidence.get("attribution_level", 0),
                claim=claim,
            )
            candidates.append(candidate)
            if candidate.level == 2:
                texts[id(candidate)] = getattr(claim, "evidence_text", None) or ""

    _demote_text_collisions(candidates, texts)
    return candidates


def winning_articles(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    commit_index,
    hotkey_by_uid: Dict[int, str],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
    judge_fn: Callable = None,
) -> List[Candidate]:
    return winning_candidates(_build_candidates(
        claims_by_uid, commitments, commit_index, hotkey_by_uid,
        briefs, registry, fetch_fn, search_fn, judge_fn,
    ))


def score_claims(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    commit_index,
    hotkey_by_uid: Dict[int, str],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
    judge_fn: Callable = None,
) -> Dict[int, float]:
    candidates = _build_candidates(
        claims_by_uid, commitments, commit_index, hotkey_by_uid,
        briefs, registry, fetch_fn, search_fn, judge_fn,
    )
    usd_by_uid = {uid: 0.0 for uid in claims_by_uid}
    usd_by_uid.update(resolve_attribution(candidates))
    return usd_by_uid
