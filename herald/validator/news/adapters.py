"""Authoritative publisher-metadata adapters for the `api:<name>` fetch strategy.

Outlets behind a bot-wall (NYT, etc.) can't be scraped by a code-only validator — the article page
returns a JS challenge. But the publisher's own API returns AUTHORITATIVE, deterministic metadata
(byline, publish date, section, abstract, lead paragraph) with no bot-wall — identical bytes for
every validator, so no cross-validator fork. The oracle anchors the miner's claim snapshot to the
API's lead paragraph (proving the snapshot really is that article), then runs the body checks on the
anchored snapshot while trusting byline/date/topic from the API.

A FetchResult with body_kind="excerpt" signals the oracle to flip the snapshot anchor direction
(the short authoritative excerpt must appear IN the snapshot, not vice-versa).
"""

import os
from datetime import datetime

import httpx

from .fetch import FetchResult, _cache, _cache_put
from .url import canonicalize

_NYT_SEARCH = "https://api.nytimes.com/svc/search/v2/articlesearch.json"


def _nyt_fetch(url: str) -> FetchResult:
    key = os.getenv("HERALD_NYT_API_KEY")
    if not key:
        return FetchResult(False, 0, url, "", 0)  # adapter disabled without a key (fail closed)
    try:
        r = httpx.get(_NYT_SEARCH, params={"fq": f'web_url:("{url}")', "api-key": key}, timeout=20.0)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
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
    published_ts = None
    pub = doc.get("pub_date")
    if pub:
        try:
            published_ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            pass
    # The anchor target is the lead paragraph (a distinctive, verbatim slice of the real article).
    excerpt = lead or abstract or headline
    topic_text = " ".join(t for t in (headline, abstract, lead, keywords, section) if t)
    return FetchResult(
        ok=bool(excerpt), status=200, final_url=url, text_hash="", body_len=len(excerpt),
        text=excerpt, published_ts=published_ts, author=author or None,
        body_kind="excerpt", topic_text=topic_text,
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
