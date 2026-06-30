"""Detect paid/sponsored content that must not be scored as real news."""

import re
from urllib.parse import urlsplit

_PAID_PATH = re.compile(
    r"/(press-?releases?|sponsored?|partner|paid-?post|advertorial|pr-?newswire|prnewswire)(/|$)",
    re.I,
)
_PAID_TEXT = re.compile(
    r"\b(paid post|sponsored content|sponsored by|advertorial|in partnership with|"
    r"presented by|this is a paid advertisement|promoted content)\b",
    re.I,
)


def is_paid(url: str, text: str):
    if _PAID_PATH.search(urlsplit(url).path):
        return True, "url_path"
    if text and _PAID_TEXT.search(text):
        return True, "disclosure_label"
    return False, ""
