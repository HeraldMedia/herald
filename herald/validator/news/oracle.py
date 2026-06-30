"""Per-article verification oracle: exact checks, cheapest-first with early-exit."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from herald.commit import matches as commitment_matches
from .fetch import fetch as default_fetch
from .scoring import article_usd
from .url import article_id


@dataclass
class ArticleResult:
    article_id: str
    brief_id: str
    usd: float
    passed: bool
    reason: str
    evidence: Dict[str, Any]


def _reject(claim, reason: str, evidence: dict) -> ArticleResult:
    return ArticleResult(article_id(claim.article_url), claim.brief_id, 0.0, False, reason, evidence)


def evaluate_article(
    claim,
    onchain_value: str,
    registry,
    brief: dict,
    fetch_fn: Callable = default_fetch,
    search_fn: Callable = None,
) -> ArticleResult:
    if search_fn is None:
        from .search import in_index
        search_fn = in_index

    evidence: Dict[str, Any] = {"version_id": claim.version_id}

    if not commitment_matches(
        onchain_value,
        brief_id=claim.brief_id,
        target_outlet_id=claim.target_outlet_id,
        claimer_hotkey=claim.claimer_hotkey,
        nonce=claim.nonce,
        bond_atto=claim.bond_atto,
        version_id=claim.version_id,
    ):
        return _reject(claim, "commitment_invalid", evidence)
    evidence["commitment"] = True

    outlet = registry.lookup(claim.article_url)
    if outlet is None:
        return _reject(claim, "outlet_not_listed", evidence)
    if outlet.outlet_id != claim.target_outlet_id:
        return _reject(claim, "outlet_mismatch", evidence)
    evidence["outlet_id"] = outlet.outlet_id
    evidence["tier"] = outlet.tier

    fr = fetch_fn(claim.article_url)
    evidence["http_status"] = fr.status
    evidence["text_hash"] = fr.text_hash
    if not fr.ok:
        return _reject(claim, "url_not_live", evidence)

    sr = search_fn(claim.article_url)
    evidence["in_index"] = sr.in_index
    evidence["matched_url"] = sr.matched_url

    usd = article_usd(outlet.tier, sr.in_index, brief.get("boost", 1.0))
    return ArticleResult(article_id(claim.article_url), claim.brief_id, usd, True, "ok", evidence)
