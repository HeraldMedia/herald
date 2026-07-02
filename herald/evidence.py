"""Attribution evidence: the secret pre-publication knowledge a miner binds into its commitment.

Shared by the miner (hash at commit time) and validator (verify + grade at claim time). The
evidence dict never touches the chain — only its hash does (inside the salted commit hash), so
pitching plans stay private until the claim reveals them.

Fields (all optional, at least one for a non-empty evidence):
  text    — draft article text or a supplied quote the miner expects to appear verbatim-ish
  author  — the expected byline
  window  — [start, end] expected publish dates, "%Y-%m-%d"
"""

import hashlib
import json
from datetime import date

MAX_TEXT_CHARS = 20_000
MAX_AUTHOR_CHARS = 120

_FIELDS = ("text", "author", "window")


def _valid_window(window) -> bool:
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return False
    try:
        start, end = (date.fromisoformat(str(d)) for d in window)
    except ValueError:
        return False
    return start <= end


def clean_evidence(evidence: dict | None) -> dict:
    """Normalize an evidence dict to its canonical subset; raises ValueError on malformed fields.

    Returns {} when nothing usable is present (callers treat {} as "no evidence").
    """
    if not evidence:
        return {}
    out: dict = {}
    text = str(evidence.get("text") or "").strip()
    if text:
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"evidence text exceeds {MAX_TEXT_CHARS} chars")
        out["text"] = text
    author = str(evidence.get("author") or "").strip()
    if author:
        if len(author) > MAX_AUTHOR_CHARS:
            raise ValueError(f"evidence author exceeds {MAX_AUTHOR_CHARS} chars")
        out["author"] = author
    window = evidence.get("window")
    if window:
        if not _valid_window(window):
            raise ValueError("evidence window must be [YYYY-MM-DD, YYYY-MM-DD] with start <= end")
        out["window"] = [str(window[0]), str(window[1])]
    return out


def canonical_evidence(evidence: dict) -> bytes:
    payload = {k: evidence[k] for k in _FIELDS if k in evidence}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_hash(evidence: dict) -> str:
    """blake2b-192 hex of the canonical evidence — the pre_hash bound into the commitment."""
    return hashlib.blake2b(canonical_evidence(evidence), digest_size=24).hexdigest()
