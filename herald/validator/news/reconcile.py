"""Claim reconciliation: pick up claims a miner served to OTHER validators but not this one.

Claims are pulled per-validator over dendrite, so a miner can serve validator A and withhold from
B, forcing their scores apart (and gaming the EMA). Winners published to the board carry the full
reveal in a token-protected validator feed — this merges those reveals back into the local claim
set. The board is only a HINT channel: every merged claim is re-verified from scratch by the
oracle (commitment vs the chain slot, evidence hash, fetch, registry), so a malicious board can
at worst add claims that fail verification, or withhold — which is no worse than today.
"""

import os
from typing import Dict, List

from herald.protocol import ClaimRecord
from herald.validator.utils.config import HERALD_MAX_ARTICLES_PER_MINER

from .url import article_id

_MAX_ROWS = 10_000  # DoS backstop on a hostile/bloated board feed


def merge_board_claims(claims_by_uid: Dict[int, list], rows: List[dict],
                       hotkey_by_uid: Dict[int, str],
                       max_per_miner: int = HERALD_MAX_ARTICLES_PER_MINER) -> int:
    """Merge published reveals into claims_by_uid (in place). Returns how many were added."""
    uid_by_hotkey = {hk: uid for uid, hk in hotkey_by_uid.items()}
    seen = {
        (uid, article_id(c.article_url))
        for uid, claims in claims_by_uid.items()
        for c in claims
        if getattr(c, "article_url", None)
    }

    added = 0
    for row in rows[:_MAX_ROWS]:
        reveal = row.get("reveal") if isinstance(row, dict) else None
        if not isinstance(reveal, dict):
            continue  # pre-hardening row (or junk): nothing to reconstruct
        uid = uid_by_hotkey.get(row.get("hotkey"))
        url = row.get("url")
        if uid is None or not isinstance(url, str) or not url:
            continue
        key = (uid, article_id(url))
        if key in seen or len(claims_by_uid.get(uid, [])) >= max_per_miner:
            continue
        try:
            # ClaimRecord validation enforces the same bounds as a dendrite response, so a
            # hostile board can't smuggle oversized fields past the protocol caps.
            claim = ClaimRecord(
                brief_id=str(row.get("brief_id") or ""),
                target_outlet_id=str(reveal.get("target_outlet_id") or ""),
                article_url=url,
                claimer_hotkey=str(row.get("hotkey") or ""),
                nonce=str(reveal.get("nonce") or ""),
                bond_atto=int(reveal.get("bond_atto") or 0),
                version_id=int(reveal.get("version_id") or 0),
                pre_hash=reveal.get("pre_hash") or None,
                evidence_text=reveal.get("evidence_text") or None,
                evidence_author=reveal.get("evidence_author") or None,
                evidence_window=reveal.get("evidence_window") or None,
                snapshot_text=reveal.get("snapshot_text") or None,
            )
        except (ValueError, TypeError):
            continue
        claims_by_uid.setdefault(uid, []).append(claim)
        seen.add(key)
        added += 1
    return added


def fetch_board_results(endpoint: str) -> List[dict]:
    """Best-effort fetch of the token-protected full-reveal feed. Failure -> []."""
    import httpx

    try:
        token = os.getenv("HERALD_RESULTS_TOKEN")
        headers = {"X-Results-Token": token} if token else {}
        resp = httpx.get(f"{endpoint.rstrip('/')}/validator/results", headers=headers,
                         timeout=10.0)
        # Compatibility with the legacy board, whose public result rows carried reveals.
        if resp.status_code == 404:
            resp = httpx.get(f"{endpoint.rstrip('/')}/public/articles", timeout=10.0)
        resp.raise_for_status()
        rows = resp.json()
        return rows if isinstance(rows, list) else []
    except Exception:
        return []
