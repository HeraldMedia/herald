"""Fetch a claimed article URL, report whether it is live, and extract its text."""

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from herald.validator.utils.config import HERALD_MIN_BODY_BYTES
from .url import canonicalize

_HEADERS = {"User-Agent": "HeraldValidator/1.0 (+https://herald.network)"}
_SKIP_TAGS = {"script", "style", "noscript"}


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


@dataclass
class FetchResult:
    ok: bool
    status: int
    final_url: str
    text_hash: str
    body_len: int
    text: str = ""


def _http_get(url: str):
    r = httpx.get(url, follow_redirects=True, timeout=20.0, headers=_HEADERS)
    return r.status_code, str(r.url), r.content


def fetch(url: str) -> FetchResult:
    canon = canonicalize(url)
    try:
        status, final_url, body = _http_get(canon)
    except Exception:
        return FetchResult(False, 0, canon, "", 0)
    ok = status == 200 and len(body) >= HERALD_MIN_BODY_BYTES
    text = _extract_text(body.decode("utf-8", "ignore"))
    return FetchResult(ok, status, final_url, hashlib.sha256(body).hexdigest(), len(body), text)
