"""Fetch a claimed article URL via a provider quorum, with epoch-keyed caching."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

import httpx

from herald.validator.utils.config import (
    HERALD_MIN_BODY_BYTES,
    HERALD_QUORUM_THRESHOLD,
    SCRAPINGBEE_API_KEY,
)
from .url import canonicalize

_HEADERS = {"User-Agent": "HeraldValidator/1.0 (+https://herald.network)"}
_SKIP_TAGS = {"script", "style", "noscript"}
_cache = {}  # (canonical_url, epoch) -> FetchResult


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            chunk = data.strip()
            if chunk:
                self.parts.append(chunk)


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return " ".join(parser.parts)


_PUBLISHED_RE = re.compile(
    r'"datePublished"\s*:\s*"([^"]+)"|article:published_time"\s+content="([^"]+)"'
)


def _parse_published_ts(html: str):
    m = _PUBLISHED_RE.search(html)
    if not m:
        return None
    raw = (m.group(1) or m.group(2)).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


@dataclass
class FetchResult:
    ok: bool
    status: int
    final_url: str
    text_hash: str
    body_len: int
    text: str = ""
    providers_live: int = 0
    published_ts: float = None


def _http_get(url: str):
    r = httpx.get(url, follow_redirects=True, timeout=20.0, headers=_HEADERS)
    return r.status_code, str(r.url), r.content


def _scrapingbee_get(url: str):
    r = httpx.get(
        "https://app.scrapingbee.com/api/v1",
        params={"api_key": SCRAPINGBEE_API_KEY, "url": url, "render_js": "false"},
        timeout=30.0,
    )
    return r.status_code, url, r.content


def _providers():
    providers = [_http_get]
    if SCRAPINGBEE_API_KEY:
        providers.append(_scrapingbee_get)
    return providers


def fetch(url: str, epoch=None) -> FetchResult:
    canon = canonicalize(url)
    if epoch is not None and (canon, epoch) in _cache:
        return _cache[(canon, epoch)]

    providers = _providers()
    results = []
    for provider in providers:
        try:
            results.append(provider(canon))
        except Exception:
            pass

    live = [r for r in results if r[0] == 200 and len(r[2]) >= HERALD_MIN_BODY_BYTES]
    ok = len(live) >= min(HERALD_QUORUM_THRESHOLD, len(providers))
    status, final_url, body = live[0] if live else (results[0] if results else (0, canon, b""))

    html = body.decode("utf-8", "ignore")
    result = FetchResult(
        ok=ok,
        status=status,
        final_url=final_url,
        text_hash=hashlib.sha256(body).hexdigest(),
        body_len=len(body),
        text=_extract_text(html),
        providers_live=len(live),
        published_ts=_parse_published_ts(html),
    )
    if epoch is not None:
        _cache[(canon, epoch)] = result
    return result
