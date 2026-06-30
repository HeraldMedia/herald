"""Canonical URL normalization. Identical across validators so attribution is deterministic."""

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_PCT = re.compile(r'%[0-9a-fA-F]{2}')

_TRACKING_EXACT = {
    "fbclid", "gclid", "gbraid", "wbraid", "msclkid",
    "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
}


def _keep_param(key: str) -> bool:
    k = key.lower()
    return not k.startswith("utm_") and k not in _TRACKING_EXACT


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".")  # drop fully-qualified trailing dot
    try:
        host = host.encode("idna").decode("ascii")  # stable host across Unicode/punycode forms
    except Exception:
        pass
    bracket = f"[{host}]" if ":" in host else host  # preserve IPv6 brackets

    netloc = bracket
    if parts.port and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        netloc = f"{bracket}:{parts.port}"

    query_pairs = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if _keep_param(k)
    )
    query = urlencode(query_pairs)

    path = _PCT.sub(lambda m: m.group(0).upper(), parts.path)  # normalize %xx case
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if path == "/":
        path = ""

    return urlunsplit((scheme, netloc, path, query, ""))


def article_id(url: str) -> str:
    return hashlib.sha256(canonicalize(url).encode("utf-8")).hexdigest()


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
