"""Per-article verification oracle: exact checks, cheapest-first with early-exit."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from herald.commit import matches as commitment_matches
from .bonds import min_bond_atto
from .fetch import fetch as default_fetch
from .real_news import is_paid
from .scoring import article_usd
from .topic_match import topic_matched
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
    judge_fn: Callable = None,
    serving_hotkey: str = None,
) -> ArticleResult:
    if search_fn is None:
        from .search import in_index
        search_fn = in_index

    evidence: Dict[str, Any] = {"version_id": claim.version_id}

    if serving_hotkey is not None and claim.claimer_hotkey != serving_hotkey:
        return _reject(claim, "hotkey_mismatch", evidence)

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

    if registry.version_id and claim.version_id != registry.version_id:
        return _reject(claim, "stale_version", evidence)

    outlet = registry.lookup(claim.article_url)
    if outlet is None:
        return _reject(claim, "outlet_not_listed", evidence)
    if outlet.outlet_id != claim.target_outlet_id:
        return _reject(claim, "outlet_mismatch", evidence)
    evidence["outlet_id"] = outlet.outlet_id
    evidence["tier"] = outlet.tier

    expected_usd = article_usd(outlet.tier, True, brief.get("boost", 1.0))
    if claim.bond_atto < min_bond_atto(expected_usd):
        return _reject(claim, "bond_too_small", evidence)

    fr = fetch_fn(claim.article_url)
    evidence["http_status"] = fr.status
    evidence["text_hash"] = fr.text_hash
    if not fr.ok:
        return _reject(claim, "url_not_live", evidence)

    paid, paid_reason = is_paid(claim.article_url, fr.text, judge_fn)
    evidence["paid"] = paid
    if paid:
        evidence["paid_reason"] = paid_reason
        return _reject(claim, "paid_not_real_news", evidence)

    if not topic_matched(fr.text, brief, judge_fn):
        evidence["topic_match"] = False
        return _reject(claim, "topic_mismatch", evidence)
    evidence["topic_match"] = True

    sr = search_fn(claim.article_url)
    evidence["in_index"] = sr.in_index
    evidence["matched_url"] = sr.matched_url

    usd = article_usd(outlet.tier, sr.in_index, brief.get("boost", 1.0))
    return ArticleResult(article_id(claim.article_url), claim.brief_id, usd, True, "ok", evidence)
