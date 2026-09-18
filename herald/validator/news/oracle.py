"""Per-article verification oracle: exact checks, cheapest first, stopping at the first failure."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from herald.validator.utils.config import (
    HERALD_DRAFT_MATCH_THRESHOLD,
    HERALD_MAX_ARTICLE_AGE_DAYS,
    HERALD_PUBLISH_BUFFER_DAYS,
)
from .real_news import is_paid
from .scoring import article_usd
from .textmatch import containment
from .topic_match import topic_matched
from .url import article_id

# Fetch strategies whose full article page this validator reads itself. Every content check runs on
# that page, so outlets verified any other way are not supported.
SUPPORTED_FETCH_STRATEGIES = ("direct", "proxy")

_DAY_SECONDS = 86400


@dataclass
class ArticleResult:
    article_id: str
    brief_id: str
    usd: float
    passed: bool
    reason: str
    evidence: Dict[str, Any]


def _brief_day(value) -> date:
    return datetime.strptime(str(value), "%Y-%m-%d").date()  # the brief feed's date format


def _utc_midnight(day: date) -> float:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp()


def published_in_window(published_ts: float, brief: dict, now_ts: float,
                        published_day: date = None) -> bool:
    """The publication-time rule.

    Always: published no later than chain time `now_ts` and at most HERALD_MAX_ARTICLE_AGE_DAYS before
    it. A brief with an end_date (standing briefs excepted) also requires publication from start_date
    minus HERALD_PUBLISH_BUFFER_DAYS at 00:00 UTC through end_date 23:59:59 UTC; a brief without a
    start_date has no lower brief bound. A publication time that is not exact passes `published_day`,
    the date the page states, and that date must fall from start_date minus the buffer through
    end_date.
    """
    if published_ts > now_ts or published_ts < now_ts - HERALD_MAX_ARTICLE_AGE_DAYS * _DAY_SECONDS:
        return False
    end = brief.get("end_date")
    if brief.get("kind") == "standing" or not end:
        return True
    last_day = _brief_day(end)
    start = brief.get("start_date")
    first_day = _brief_day(start) - timedelta(days=HERALD_PUBLISH_BUFFER_DAYS) if start else None
    if published_day is not None:
        return published_day <= last_day and (first_day is None or published_day >= first_day)
    if published_ts >= _utc_midnight(last_day + timedelta(days=1)):
        return False
    if first_day is not None and published_ts < _utc_midnight(first_day):
        return False
    return True


def _utc_day(ts: float) -> date:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).date()


def published_after_upload(published_ts: float, uploaded_ts: float, exact: bool = False,
                           published_day: date = None) -> bool:
    """The article was published no earlier than the draft upload.

    With an exact publication time (a time of day and an explicit UTC offset) the upload must be at or
    before it. Otherwise the publication day must be the upload's UTC day or later: many outlets state
    only a publication date, or a time with no offset. The publication day is `published_day`, the
    date the page states, when given, and the UTC day of `published_ts` otherwise.
    """
    if exact:
        return float(uploaded_ts) <= float(published_ts)
    day = published_day if published_day is not None else _utc_day(published_ts)
    return day >= _utc_day(uploaded_ts)


def _stated_day(value) -> Optional[date]:
    """The YYYY-MM-DD date a page states for a publication time that is not exact, or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def draft_match(draft_text: str, article_text: str) -> float:
    """Share of the draft's word shingles found in the article body (textmatch containment)."""
    return containment(draft_text or "", article_text or "")


def verify_article(
    url: str,
    brief: dict,
    registry,
    fetch_fn: Callable,
    search_fn: Callable,
    judge_fn: Callable,
    now_ts: float,
    *,
    draft_text: str,
    uploaded_ts: int,
) -> ArticleResult:
    """Verify one submitted article against its brief and the draft uploaded before publication.

    Checks, in order: listed outlet, supported fetch strategy, live page, publication date present and
    inside the window, published after the upload (at or after the upload time when the page states an
    exact time, otherwise on or after the upload's UTC day), the uploaded draft found in the article
    body (at least HERALD_DRAFT_MATCH_THRESHOLD), not paid content, on topic. A publication time that
    is not exact is placed by the date the page states, for both the window and the upload day. A
    passing article is valued by its outlet tier and search presence. The draft itself is never added
    to the evidence.
    """
    brief_id = str(brief.get("id", ""))
    evidence: Dict[str, Any] = {}

    def reject(reason: str) -> ArticleResult:
        return ArticleResult(article_id(url), brief_id, 0.0, False, reason, evidence)

    outlet = registry.lookup(url)
    if outlet is None:
        return reject("outlet_not_listed")
    evidence["outlet_id"] = outlet.outlet_id
    evidence["tier"] = outlet.tier
    if (outlet.fetch or "direct").split(":")[0] not in SUPPORTED_FETCH_STRATEGIES:
        return reject("outlet_not_supported")

    fr = fetch_fn(url)
    evidence["http_status"] = fr.status
    evidence["text_hash"] = getattr(fr, "text_hash", "")
    if not fr.ok:
        return reject("url_not_live")

    published_ts = getattr(fr, "published_ts", None)
    published_exact = getattr(fr, "published_exact", False) is True
    # A publication time that is not exact is placed by the date the page states, in both day rules.
    published_day = None if published_exact else _stated_day(getattr(fr, "published_date", None))
    evidence["published_ts"] = published_ts
    evidence["published_exact"] = published_exact
    evidence["published_date"] = published_day.isoformat() if published_day is not None else None
    if published_ts is None:
        return reject("publication_date_unverifiable")
    if not published_in_window(float(published_ts), brief, float(now_ts), published_day=published_day):
        return reject("published_outside_window")
    if not published_after_upload(float(published_ts), uploaded_ts, exact=published_exact,
                                  published_day=published_day):
        return reject("published_before_upload")

    article_text = getattr(fr, "article_text", None) or fr.text
    share = draft_match(draft_text, article_text)
    evidence["draft_match"] = round(share, 3)
    if share < HERALD_DRAFT_MATCH_THRESHOLD:
        return reject("draft_mismatch")

    paid, paid_reason = is_paid(url, article_text, judge_fn, outlet=outlet)
    evidence["paid"] = paid
    if paid:
        evidence["paid_reason"] = paid_reason
        return reject("paid_not_real_news")

    on_topic = topic_matched(article_text, brief, judge_fn)
    evidence["topic_match"] = on_topic
    if not on_topic:
        return reject("topic_mismatch")

    sr = search_fn(url)
    evidence["in_index"] = sr.in_index
    evidence["matched_url"] = getattr(sr, "matched_url", None)
    usd = article_usd(outlet.tier, sr.in_index)
    return ArticleResult(article_id(url), brief_id, usd, True, "ok", evidence)
