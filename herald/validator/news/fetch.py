"""Fetch a claimed article URL via a provider quorum, with epoch-keyed caching."""

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from herald.validator.utils.config import (
    HERALD_MAX_BODY_BYTES,
    HERALD_MIN_BODY_BYTES,
    HERALD_QUORUM_THRESHOLD,
    SCRAPINGBEE_API_KEY,
)
from .url import canonicalize


def _ip_blocked(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolve_ips(host: str):
    import socket
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard: http(s) only, and the host must resolve only to public IPs.

    Resolving catches decimal/hex/octal IP encodings (e.g. http://2130706433/ -> 127.0.0.1)
    and hostnames that point at internal addresses, not just literal dotted-quad IPs.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        return not _ip_blocked(host)  # literal IP
    except ValueError:
        pass
    try:
        ips = _resolve_ips(host)
    except Exception:
        return False  # unresolvable -> block
    return bool(ips) and all(not _ip_blocked(ip) for ip in ips)

_HEADERS = {"User-Agent": "HeraldValidator/1.0 (+https://herald.network)"}
_SKIP_TAGS = {"script", "style", "noscript"}
_cache = {}  # (canonical_url, epoch) -> FetchResult
_CACHE_MAX = 50000


def _cache_put(cache, key, value):
    cache[key] = value
    while len(cache) > _CACHE_MAX:
        cache.pop(next(iter(cache)))


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


_PUBLISHED_PATTERNS = [
    re.compile(r'["\']datePublished["\']\s*:\s*["\']([^"\']+)["\']'),
    re.compile(r'(?:article:published_time|og:published_time)["\']?\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]*?(?:article:published_time|og:published_time)', re.I),
]


def _parse_published_ts(html: str):
    for pattern in _PUBLISHED_PATTERNS:
        m = pattern.search(html)
        if not m:
            continue
        raw = m.group(1).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # naive dates are UTC for ALL validators
        return dt.timestamp()
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
    # Follow redirects manually so each hop is SSRF-checked BEFORE we connect to it,
    # and stream the body so a huge response can't exhaust memory.
    current = url
    for _ in range(5):
        if not is_safe_fetch_url(current):
            raise ValueError(f"unsafe fetch target: {current}")
        with httpx.stream("GET", current, follow_redirects=False, timeout=20.0, headers=_HEADERS) as r:
            if r.is_redirect and r.next_request is not None:
                current = str(r.next_request.url)
                continue
            chunks, total = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= HERALD_MAX_BODY_BYTES:
                    break
            return r.status_code, str(r.url), b"".join(chunks)[:HERALD_MAX_BODY_BYTES]
    raise ValueError("too many redirects")


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

    if not is_safe_fetch_url(canon):
        result = FetchResult(False, 0, canon, "", 0)
        if epoch is not None:
            _cache_put(_cache, (canon, epoch), result)
        return result

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
        _cache_put(_cache, (canon, epoch), result)
    return result
