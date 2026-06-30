"""Check whether a claimed article URL appears in a public search index."""

from dataclasses import dataclass
from typing import List, Optional

import httpx

from herald.validator.utils.config import (
    HERALD_SEARCH_TOP_N,
    SERPAPI_API_KEY,
)
from .url import canonicalize


@dataclass
class SearchResult:
    in_index: bool
    query: str
    matched_url: Optional[str]
    num_results: int


def _serpapi_search(query: str, num: int) -> List[str]:
    params = {
        "engine": "google",
        "q": query,
        "num": num,
        "gl": "us",
        "hl": "en",
        "api_key": SERPAPI_API_KEY,
    }
    r = httpx.get("https://serpapi.com/search.json", params=params, timeout=20.0)
    r.raise_for_status()
    data = r.json()
    return [item.get("link") for item in data.get("organic_results", []) if item.get("link")]


def in_index(article_url: str) -> SearchResult:
    target = canonicalize(article_url)
    try:
        links = _serpapi_search(target, HERALD_SEARCH_TOP_N)
    except Exception:
        return SearchResult(False, target, None, 0)

    matched = next((c for c in (canonicalize(l) for l in links) if c == target), None)
    return SearchResult(matched is not None, target, matched, len(links))
