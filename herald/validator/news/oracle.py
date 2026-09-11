"""Per-article verification oracle: exact checks, cheapest-first with early-exit."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from herald.commit import matches as commitment_matches
from herald.evidence import clean_evidence, evidence_hash
from herald.validator.utils.config import HERALD_ATTR_MULT, HERALD_SNAPSHOT_ANCHOR
from .fetch import fetch as default_fetch
from .real_news import is_paid
from .scoring import article_usd
from .textmatch import containment, grade_evidence
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

    # Attribution evidence (optional): the reveal must hash to the pre_hash bound into the
    # commitment, so everything in it was fixed before the article existed.
    pre_hash = getattr(claim, "pre_hash", None) or ""
    try:
        attr_evidence = clean_evidence({
            "text": getattr(claim, "evidence_text", None),
            "author": getattr(claim, "evidence_author", None),
            "window": getattr(claim, "evidence_window", None),
        })
    except ValueError:
        return _reject(claim, "evidence_invalid", evidence)
    if attr_evidence and evidence_hash(attr_evidence) != pre_hash:
        return _reject(claim, "evidence_hash_mismatch", evidence)

    if not commitment_matches(
        onchain_value,
        brief_id=claim.brief_id,
        target_outlet_id=claim.target_outlet_id,
        claimer_hotkey=claim.claimer_hotkey,
        nonce=claim.nonce,
        bond_atto=claim.bond_atto,
        version_id=claim.version_id,
        pre_hash=pre_hash,
    ):
        return _reject(claim, "commitment_invalid", evidence)
    evidence["commitment"] = True

    if claim.version_id != registry.version_id:
        return _reject(claim, "stale_version", evidence)

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

    # The content checks score the article this validator fetched. A claim may also carry a snapshot
    # of the page text the miner saw: it only has to match our fetch (a failed anchor rejects only
    # this pass) and is never the text that gets graded. The anchor DIRECTION depends on the
    # outlet's fetch strategy:
    #   • full body (direct/proxy): the snapshot must be contained in our full fetch (snapshot ⊆ body),
    #     and the paid-content, topic and attribution checks all run on the article we fetched.
    #   • excerpt (api:*): we hold only an authoritative excerpt, so it must appear IN the snapshot
    #     (excerpt ⊆ snapshot) — that proves the miner's snapshot really is this article, while
    #     byline/date/topic come from the unfakeable API. With no article body of our own,
    #     attribution is graded on byline and publish window only.
    snapshot = (getattr(claim, "snapshot_text", None) or "").strip()
    body_kind = getattr(fr, "body_kind", "full")
    if body_kind == "excerpt":
        if not snapshot:
            return _reject(claim, "snapshot_required", evidence)
        anchor = containment(fr.text or "", snapshot)
        evidence["snapshot_anchor"] = round(anchor, 3)
        if anchor < HERALD_SNAPSHOT_ANCHOR:
            return _reject(claim, "snapshot_mismatch", evidence)
        # No full body of our own here; the /paidpost/-style URL path check in is_paid still
        # applies, and premium outlets disclose sponsored content on the path.
        paid_text = snapshot
        topic_input = getattr(fr, "topic_text", None) or fr.text  # authoritative, unfakeable
        article_text = None
    else:
        if snapshot:
            anchor = containment(snapshot, fr.text or "")
            evidence["snapshot_anchor"] = round(anchor, 3)
            if anchor < HERALD_SNAPSHOT_ANCHOR:
                return _reject(claim, "snapshot_mismatch", evidence)
        article_text = getattr(fr, "article_text", None) or fr.text
        paid_text = article_text
        topic_input = article_text

    paid, paid_reason = is_paid(claim.article_url, paid_text, judge_fn, outlet=outlet)
    evidence["paid"] = paid
    if paid:
        evidence["paid_reason"] = paid_reason
        return _reject(claim, "paid_not_real_news", evidence)

    if not topic_matched(topic_input, brief, judge_fn):
        evidence["topic_match"] = False
        return _reject(claim, "topic_mismatch", evidence)
    evidence["topic_match"] = True

    sr = search_fn(claim.article_url)
    evidence["in_index"] = sr.in_index
    evidence["matched_url"] = sr.matched_url

    # Grade the pre-committed evidence against the published article: the level's multiplier
    # prices how strongly this claim proves the miner CAUSED the coverage (vs predicted it).
    graded_evidence = attr_evidence
    if body_kind == "excerpt":
        # A text proof needs an article body this validator fetched itself, which an excerpt
        # outlet does not provide: grade the byline and publish window only.
        graded_evidence = {k: v for k, v in attr_evidence.items() if k != "text"}
    level, attr_detail = grade_evidence(graded_evidence, fr, brief, article_text=article_text)
    evidence["attribution_level"] = level
    evidence.update({f"attribution_{k}": v for k, v in attr_detail.items()})

    usd = article_usd(outlet.tier, sr.in_index) * HERALD_ATTR_MULT.get(level, 0.0)
    return ArticleResult(article_id(claim.article_url), claim.brief_id, usd, True, "ok", evidence)
