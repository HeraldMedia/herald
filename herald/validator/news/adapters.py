"""Authoritative publisher-metadata adapters for the `api:<name>` fetch strategy.

Outlets behind a bot-wall (NYT, etc.) can't be scraped by a code-only validator — the article page
returns a JS challenge. But the publisher's own API returns AUTHORITATIVE, deterministic metadata
(byline, publish date, section, abstract, lead paragraph) with no bot-wall — identical bytes for
every validator, so no cross-validator fork. The oracle anchors the miner's claim snapshot to the
API's lead paragraph (proving the snapshot really is that article), then runs the paid-content check
on the anchored snapshot while trusting byline/date/topic from the API. Holding no article body of its
own for these outlets, the oracle grades their attribution on byline and publish window only.

A FetchResult with body_kind="excerpt" signals the oracle to flip the snapshot anchor direction
(the short authoritative excerpt must appear IN the snapshot, not vice-versa).
"""

import os
from urllib.parse import urlsplit

import httpx

from herald.validator.utils.config import HERALD_NYT_API_BASE
from .fetch import FetchResult, _cache, _cache_put, _parse_published_value
from .url import canonicalize

_NYT_SEARCH = HERALD_NYT_API_BASE  # real Article Search API by default; overridable for a localhost sim


def _url_slug(url: str) -> str:
    # NYT article URLs end in a hyphenated slug, e.g. .../kratom-trump-administration.html — turn it
    # into search words. Used as the `q` query (not embedded in an fq filter), so a hostile URL can't
    # inject Lucene syntax; the exact match happens client-side on web_url.
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail[:-5].replace("-", " ").strip() if tail.endswith(".html") else tail.replace("-", " ").strip()


def _nyt_fetch(url: str) -> FetchResult:
    key = os.getenv("HERALD_NYT_API_KEY")
    if not key:
        return FetchResult(False, 0, url, "", 0)  # adapter disabled without a key (fail closed)
    # NYT's `fq=web_url:(...)` exact filter is unreliable (returns 0 hits for valid URLs), so query by
    # the article's slug and match web_url in the results client-side.
    try:
        r = httpx.get(_NYT_SEARCH, params={"q": _url_slug(url), "api-key": key}, timeout=20.0)
        r.raise_for_status()
        # `docs` can be null (not just absent) when the API throttles or returns nothing — coerce
        # to [] so the exact-match below can't crash the whole scoring pass on a NoneType.
        docs = (r.json().get("response") or {}).get("docs") or []
    except Exception:
        return FetchResult(False, 0, url, "", 0)
    doc = next((d for d in docs if canonicalize(d.get("web_url", "") or "") == canonicalize(url)), None)
    if doc is None:
        return FetchResult(False, 404, url, "", 0)  # not in NYT's index -> treat as not live
    return _from_nyt_doc(url, doc)


def _from_nyt_doc(url: str, doc: dict) -> FetchResult:
    lead = (doc.get("lead_paragraph") or "").strip()
    headline = ((doc.get("headline") or {}).get("main") or "").strip()
    abstract = (doc.get("abstract") or doc.get("snippet") or "").strip()
    section = (doc.get("section_name") or "").strip()
    keywords = " ".join(k.get("value", "") for k in (doc.get("keywords") or []) if isinstance(k, dict))
    byline = ((doc.get("byline") or {}).get("original") or "").strip()
    author = byline[3:].strip() if byline[:3].lower() == "by " else (byline or None)
    # Read like a page's publication time: exact only with an explicit offset and a local time of day
    # other than exactly midnight; otherwise the date it states is carried for the day rules.
    published_ts, published_exact, published_date = None, False, None
    pub = doc.get("pub_date")
    parsed = _parse_published_value(pub) if isinstance(pub, str) else None
    if parsed is not None:
        published_ts, published_exact, published_date = parsed
    # The anchor target is the lead paragraph (a distinctive, verbatim slice of the real article).
    excerpt = lead or abstract or headline
    topic_text = " ".join(t for t in (headline, abstract, lead, keywords, section) if t)
    return FetchResult(
        ok=bool(excerpt), status=200, final_url=url, text_hash="", body_len=len(excerpt),
        text=excerpt, published_ts=published_ts, published_exact=published_exact,
        published_date=published_date, author=author or None, body_kind="excerpt",
        topic_text=topic_text,
    )


_ADAPTERS = {"nyt": _nyt_fetch}


def api_fetch(name: str, url: str, epoch=None) -> FetchResult:
    """Fetch authoritative metadata for `url` via the named adapter, epoch-cached like fetch()."""
    canon = canonicalize(url)
    cache_key = (f"api:{name}", canon, epoch)
    if epoch is not None and cache_key in _cache:
        return _cache[cache_key]
    adapter = _ADAPTERS.get(name)
    result = adapter(canon) if adapter else FetchResult(False, 0, canon, "", 0)
    if epoch is not None:
        _cache_put(_cache, cache_key, result)
    return result
