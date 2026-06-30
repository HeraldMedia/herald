"""Fetch a claimed article URL and report whether it is live."""

import hashlib
from dataclasses import dataclass

import httpx

from herald.validator.utils.config import HERALD_MIN_BODY_BYTES
from .url import canonicalize

_HEADERS = {"User-Agent": "HeraldValidator/1.0 (+https://herald.network)"}


@dataclass
class FetchResult:
    ok: bool
    status: int
    final_url: str
    text_hash: str
    body_len: int


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
    return FetchResult(ok, status, final_url, hashlib.sha256(body).hexdigest(), len(body))
