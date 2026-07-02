"""Attribution-evidence grading: deterministic checks that pre-committed knowledge matches the
published article. Every check is exact string/date math (no LLM, no external calls), so honest
validators grade identically given the same fetched page.

Levels (multipliers in config, consensus-critical):
  2 — text proof: the committed draft/quote appears in the article (shingle containment)
  1 — insider detail: committed byline AND a tight publish window both match
  0 — bare commit (or evidence that doesn't verify)
"""

import unicodedata
from datetime import date, datetime, timezone

from herald.validator.utils.config import (
    HERALD_ATTR_MAX_WINDOW_DAYS,
    HERALD_ATTR_MIN_TEXT_WORDS,
    HERALD_ATTR_TEXT_THRESHOLD,
)

# Evidence text mostly lifted from the brief itself proves nothing — the brief is public.
# Checked with short (3-word) shingles: brief copy is brief, so 5-word shingles barely exist in it.
_BRIEF_OVERLAP_MAX = 0.5
_BRIEF_SHINGLE_WORDS = 3
_SHINGLE_WORDS = 5


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _shingles(words: list, n: int) -> set:
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def containment(needle: str, haystack: str, n: int = _SHINGLE_WORDS) -> float:
    """Fraction of the needle's word shingles found in the haystack (both normalized).

    Needle-sided, so a short quote scores against a long article; n shrinks for short needles.
    """
    nw = normalize_text(needle).split()
    hw = normalize_text(haystack).split()
    if not nw:
        return 0.0
    n = min(n, len(nw))
    ns = _shingles(nw, n)
    hs = _shingles(hw, n)
    return len(ns & hs) / len(ns)


def _window_matches(window, published_ts) -> bool:
    if not window or len(window) != 2 or published_ts is None:
        return False
    try:
        start, end = (date.fromisoformat(str(d)) for d in window)
    except ValueError:
        return False
    if (end - start).days > HERALD_ATTR_MAX_WINDOW_DAYS:
        return False  # too loose to count as insider knowledge
    published = datetime.fromtimestamp(published_ts, tz=timezone.utc).date()
    return start <= published <= end


def grade_evidence(evidence: dict, fetch_result, brief: dict, article_text: str = None) -> tuple:
    """Grade revealed evidence against the fetched article. Returns (level, detail dict).

    The caller has already verified evidence_hash(evidence) == the committed pre_hash, so
    everything here was fixed before publication. `article_text` overrides the fetched text
    (the oracle passes the anchored claim snapshot so every validator grades identical bytes).
    """
    if not evidence:
        return 0, {}

    text = evidence.get("text") or ""
    words = normalize_text(text).split()
    if len(words) >= HERALD_ATTR_MIN_TEXT_WORDS:
        score = containment(text, article_text if article_text is not None else (fetch_result.text or ""))
        brief_copy = " ".join(
            [str(brief.get("title") or "")] + [str(m) for m in (brief.get("messages") or brief.get("keywords") or [])]
        )
        brief_overlap = containment(text, brief_copy, n=_BRIEF_SHINGLE_WORDS) if brief_copy.strip() else 0.0
        if score >= HERALD_ATTR_TEXT_THRESHOLD and brief_overlap < _BRIEF_OVERLAP_MAX:
            return 2, {"containment": round(score, 3)}

    author = evidence.get("author") or ""
    if author and fetch_result.author and normalize_text(author) == normalize_text(fetch_result.author):
        if _window_matches(evidence.get("window"), fetch_result.published_ts):
            return 1, {"author": fetch_result.author}

    return 0, {}
