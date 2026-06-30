"""Canonical URL normalization. Identical across validators so attribution is deterministic."""

import hashlib
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

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
    host = parts.hostname or ""

    netloc = host
    if parts.port and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        netloc = f"{host}:{parts.port}"

    query_pairs = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if _keep_param(k)
    )
    query = urlencode(query_pairs)

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if path == "/":
        path = ""

    return urlunsplit((scheme, netloc, path, query, ""))


def article_id(url: str) -> str:
    return hashlib.sha256(canonicalize(url).encode("utf-8")).hexdigest()


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
