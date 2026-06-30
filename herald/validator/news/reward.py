"""Score miners' claims into per-UID USD, the input to the emission/weight pipeline."""

from typing import Callable, Dict, List

from .fetch import fetch as default_fetch
from .oracle import evaluate_article


def score_claims(
    claims_by_uid: Dict[int, list],
    commitments: Dict[str, str],
    hotkey_by_uid: Dict[int, str],
    briefs: List[dict],
    registry,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
) -> Dict[int, float]:
    briefs_by_id = {b["id"]: b for b in briefs}
    usd_by_uid: Dict[int, float] = {}

    for uid, claims in claims_by_uid.items():
        onchain = commitments.get(hotkey_by_uid.get(uid, ""), "")
        total = 0.0
        for claim in claims:
            brief = briefs_by_id.get(claim.brief_id)
            if brief is None:
                continue
            result = evaluate_article(claim, onchain, registry, brief, fetch_fn, search_fn)
            total += result.usd
        usd_by_uid[uid] = total

    return usd_by_uid
