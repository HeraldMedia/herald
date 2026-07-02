"""Check whether a claimed article URL is in a search index, via a provider quorum."""

from dataclasses import dataclass
from typing import List, Optional

import httpx

from herald.validator.utils.config import (
    BRAVE_API_KEY,
    HERALD_QUORUM_THRESHOLD,
    HERALD_SEARCH_TOP_N,
    SERPAPI_API_KEY,
)
from .url import canonicalize

_cache = {}  # (canonical_url, epoch) -> SearchResult
_CACHE_MAX = 50000


def _cache_put(cache, key, value):
    cache[key] = value
    while len(cache) > _CACHE_MAX:
        cache.pop(next(iter(cache)))


@dataclass
class SearchResult:
    in_index: bool
    query: str
    matched_url: Optional[str]
    num_results: int
    providers_matched: int = 0


def _serpapi_search(query: str, num: int) -> List[str]:
    r = httpx.get(
        "https://serpapi.com/search.json",
        params={"engine": "google", "q": query, "num": num,
                "gl": "us", "hl": "en", "api_key": SERPAPI_API_KEY},
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()
    return [i.get("link") for i in data.get("organic_results", []) if i.get("link")]


def _brave_search(query: str, num: int) -> List[str]:
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": num},
        headers={"X-Subscription-Token": BRAVE_API_KEY},
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()
    return [i.get("url") for i in data.get("web", {}).get("results", []) if i.get("url")]


def _providers():
    # Either provider can stand alone (use Brave INSTEAD of SerpAPI by setting only BRAVE_API_KEY);
    # both configured enables a cross-validator quorum. The set is CONSENSUS-critical (SerpAPI and
    # Brave return different indexes) and is stamped into the fingerprint.
    providers = []
    if SERPAPI_API_KEY:
        providers.append(_serpapi_search)
    if BRAVE_API_KEY:
        providers.append(_brave_search)
    return providers


def in_index(article_url: str, epoch=None) -> SearchResult:
    target = canonicalize(article_url)
    if epoch is not None and (target, epoch) in _cache:
        return _cache[(target, epoch)]

    providers = _providers()
    if not providers:
        # No search provider configured: we can't confirm indexing, so fail to "not indexed"
        # (the article still earns the HERALD_NO_SEARCH_FLOOR, never full index credit).
        result = SearchResult(in_index=False, query=target, matched_url=None, num_results=0)
        if epoch is not None:
            _cache_put(_cache, (target, epoch), result)
        return result

    matched_in = 0
    total_results = 0
    for provider in providers:
        try:
            links = [l for l in provider(target, HERALD_SEARCH_TOP_N) if isinstance(l, str)]
        except Exception:
            continue  # a non-list or other malformed response just doesn't contribute
        total_results += len(links)
        if any(canonicalize(l) == target for l in links):
            matched_in += 1

    in_idx = matched_in >= min(HERALD_QUORUM_THRESHOLD, len(providers))
    result = SearchResult(
        in_index=in_idx,
        query=target,
        matched_url=target if in_idx else None,
        num_results=total_results,
        providers_matched=matched_in,
    )
    if epoch is not None:
        _cache_put(_cache, (target, epoch), result)
    return result
