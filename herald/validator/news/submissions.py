"""Contributor submissions: read the backend feed, validate its rows and choose which to verify.

``GET /api/v4/validator/submissions`` returns rows ``{submission_id, network, netuid, brief_id, url,
draft_text, uploaded_ts}``: the text the contributor uploaded before publishing, and the unix time (UTC
seconds) of that upload. Every row is validated here, and each chosen article is verified by the oracle
before anything vests. Several contributors may submit the same article: its submissions are tried
earliest upload first, and the first that passes every check is credited. Draft text is used only for
verification; it is never published, stored or logged.
"""

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import bittensor as bt
import httpx

from herald.validator.utils.config import (
    HERALD_MAX_CANDIDATES_PER_ARTICLE,
    HERALD_MAX_SUBMISSIONS_PER_EPOCH,
)
from .publish import RESULTS_READ_TOKEN_ENV, results_headers
from .url import article_id, canonicalize

FEED_PATH = "/api/v4/validator/submissions"
FEED_TIMEOUT_SECONDS = 10.0
MAX_FEED_ROWS = 10_000
MAX_URL_LENGTH = 2048
MIN_DRAFT_CHARS = 300
MAX_DRAFT_CHARS = 40_000
ROW_KEYS = ("submission_id", "network", "netuid", "brief_id", "url", "draft_text", "uploaded_ts")

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_URL_CHARACTERS = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no whitespace


def fetch_submissions(endpoint: str, network: str, netuid: int) -> Optional[list]:
    """The feed's rows, or None when the feed cannot be read or its body is not a JSON list."""
    if not endpoint:
        bt.logging.warning("Submissions feed not configured: HERALD_RESULTS_ENDPOINT is unset")
        return None
    try:
        response = httpx.get(
            endpoint.rstrip("/") + FEED_PATH,
            params={"network": str(network), "netuid": int(netuid)},
            headers=results_headers(RESULTS_READ_TOKEN_ENV),
            timeout=FEED_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        bt.logging.warning(f"Submissions feed read failed: {exc}")
        return None
    if not isinstance(body, list):
        bt.logging.warning("Submissions feed body is not a list")
        return None
    return body


def _valid_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _valid_url(url) -> bool:
    """An ASCII https URL of at most MAX_URL_LENGTH characters whose canonical form has no query."""
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH or not _URL_CHARACTERS.fullmatch(url):
        return False
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return False
        return urlsplit(canonicalize(url)).query == ""
    except ValueError:
        return False


def _valid_draft(text) -> bool:
    """A string of MIN_DRAFT_CHARS to MAX_DRAFT_CHARS characters once surrounding whitespace is stripped."""
    return isinstance(text, str) and MIN_DRAFT_CHARS <= len(text.strip()) <= MAX_DRAFT_CHARS


def _valid_upload_time(uploaded_ts, now_ts: float) -> bool:
    """Integer unix seconds after the epoch and no later than chain time."""
    return type(uploaded_ts) is int and 0 < uploaded_ts <= now_ts


def validate_rows(rows: list, network: str, netuid: int, now_ts: float) -> List[dict]:
    """Rows that are well formed and belong to this network and netuid, from the first MAX_FEED_ROWS.

    A row needs a draft of MIN_DRAFT_CHARS to MAX_DRAFT_CHARS characters (after stripping) and an
    upload time no later than chain time `now_ts`. Each kept row carries exactly ROW_KEYS.
    """
    valid = []
    for row in rows[:MAX_FEED_ROWS]:
        if not isinstance(row, dict):
            continue
        row_netuid = row.get("netuid")
        if row.get("network") != str(network) or type(row_netuid) is not int or row_netuid != int(netuid):
            continue
        if not (_valid_id(row.get("submission_id")) and _valid_id(row.get("brief_id"))):
            continue
        if not _valid_url(row.get("url")):
            continue
        if not (_valid_draft(row.get("draft_text")) and _valid_upload_time(row.get("uploaded_ts"), now_ts)):
            continue
        valid.append({key: row[key] for key in ROW_KEYS})
    return valid


def select_new(rows: List[dict], vesting, limit: int = None,
               candidates: int = None) -> List[Tuple[str, List[dict]]]:
    """Choose the articles to verify this epoch, as ``(article_id, candidate rows)`` pairs.

    Validated rows are grouped by article_id. Articles the vesting ledger already holds, in any
    status, are skipped whatever their rows. An article's candidates are ordered by (uploaded_ts,
    submission_id), and only the first `candidates` (HERALD_MAX_CANDIDATES_PER_ARTICLE by default)
    are kept. Articles are ordered by their earliest candidate's upload time, then article_id, and at
    most `limit` articles (HERALD_MAX_SUBMISSIONS_PER_EPOCH by default) are returned.
    """
    limit = HERALD_MAX_SUBMISSIONS_PER_EPOCH if limit is None else limit
    candidates = HERALD_MAX_CANDIDATES_PER_ARTICLE if candidates is None else candidates
    by_article: Dict[str, List[dict]] = {}
    for row in rows:
        by_article.setdefault(article_id(row["url"]), []).append(row)
    selected: List[Tuple[str, List[dict]]] = []
    for aid, group in by_article.items():
        if vesting.has(aid):
            continue
        group = sorted(group, key=lambda row: (row["uploaded_ts"], row["submission_id"]))
        group = group[:max(0, candidates)]
        if group:
            selected.append((aid, group))
    selected.sort(key=lambda item: (item[1][0]["uploaded_ts"], item[0]))
    return selected[:max(0, limit)]
