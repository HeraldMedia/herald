"""Detect paid/sponsored content that must not be scored as real news."""

import re
from urllib.parse import urlsplit

_PAID_PATH = re.compile(
    r"(press-?releases?|sponsored?(-?content|-?post)?|sponsors?|/partner|paid-?(post|content)|"
    r"advertorial|advertising-feature|brand-?(voice|studio|connect|room|lab)|branded-?content|"
    r"paidpost|pr-?newswire|prnewswire|businesswire|globenewswire|/promoted)(/|$|-)",
    re.I,
)
_PAID_TEXT = re.compile(
    r"\b(paid post|sponsored content|sponsored by|advertorial|in partnership with|"
    r"presented by|this is a paid advertisement|promoted content|paid for by|paid program|"
    r"brandvoice|brand studio|advertising feature)\b",
    re.I,
)
_DISCLOSURE_WINDOW = 750
_LONG_MARKER_MIN = 50


def _has_generic_disclosure(text: str) -> bool:
    window = text[:_DISCLOSURE_WINDOW]
    for match in _PAID_TEXT.finditer(window):
        # A publisher label is commonly just "Sponsored Content" (or followed by the
        # sponsor name).  The grammatical sandwich "as sponsored content as ..." is
        # reported prose about disclosure practices, not a disclosure on this article.
        if match.group(0).lower() == "sponsored content":
            before = window[max(0, match.start() - 3):match.start()].lower()
            after = window[match.end():match.end() + 3].lower()
            if before == "as " and after == " as":
                continue
        return True
    return False


def is_paid(url: str, text: str, judge_fn=None, outlet=None):
    path = urlsplit(url).path
    # Per-outlet programs first (the signed registry knows each Tier-1 outlet's branded content).
    if outlet is not None:
        for pat in getattr(outlet, "paid_patterns", ()):
            if re.search(pat, path, re.I):
                return True, "outlet_paid_pattern"
        if text:
            folded = text.lower()
            for marker in getattr(outlet, "paid_markers", ()):
                # Short labels such as "in association with" are meaningful as a
                # disclosure near the article heading, but commonly occur in ordinary
                # prose or related-links navigation later in the page.  Long, distinctive
                # publisher disclaimers remain authoritative wherever they occur.
                marker = marker.strip()
                haystack = folded if len(marker) >= _LONG_MARKER_MIN else folded[:_DISCLOSURE_WINDOW]
                if marker and marker.lower() in haystack:
                    return True, "outlet_paid_marker"
    if _PAID_PATH.search(path):
        return True, "url_path"
    if text and _has_generic_disclosure(text):
        return True, "disclosure_label"
    if judge_fn is not None:
        from .judge import PAID_QUESTION
        if judge_fn(PAID_QUESTION, text) is True:
            return True, "llm"
    return False, ""
