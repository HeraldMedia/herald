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


def is_paid(url: str, text: str, judge_fn=None, outlet=None):
    path = urlsplit(url).path
    # Per-outlet programs first (the signed registry knows each Tier-1 outlet's branded content).
    if outlet is not None:
        for pat in getattr(outlet, "paid_patterns", ()):
            if re.search(pat, path, re.I):
                return True, "outlet_paid_pattern"
        if text:
            for marker in getattr(outlet, "paid_markers", ()):
                if marker and marker.lower() in text.lower():
                    return True, "outlet_paid_marker"
    if _PAID_PATH.search(path):
        return True, "url_path"
    if text and _PAID_TEXT.search(text):
        return True, "disclosure_label"
    if judge_fn is not None:
        from .judge import PAID_QUESTION
        if judge_fn(PAID_QUESTION, text) is True:
            return True, "llm"
    return False, ""
