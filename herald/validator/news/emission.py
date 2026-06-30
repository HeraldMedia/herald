"""Convert per-UID USD into a weight vector, burning the unclaimed remainder."""

from typing import Dict, List, Tuple

import numpy as np


def apply_brief_caps(
    usd_by_uid_brief: Dict[Tuple[int, str], float],
    briefs: List[dict],
    total_daily_usd: float,
) -> Dict[int, float]:
    """Cap each brief's share of daily emissions, then flatten to per-UID USD."""
    usd_by_uid: Dict[int, float] = {}
    if total_daily_usd <= 0:
        for (uid, _), usd in usd_by_uid_brief.items():
            usd_by_uid[uid] = usd_by_uid.get(uid, 0.0) + usd
        return usd_by_uid

    cap_fraction = {b["id"]: b.get("cap", 1.0) for b in briefs}
    brief_totals: Dict[str, float] = {}
    for (_, brief_id), usd in usd_by_uid_brief.items():
        brief_totals[brief_id] = brief_totals.get(brief_id, 0.0) + usd

    scale: Dict[str, float] = {}
    for brief_id, total in brief_totals.items():
        cap_usd = cap_fraction.get(brief_id, 1.0) * total_daily_usd
        scale[brief_id] = min(1.0, cap_usd / total) if total > 0 else 1.0

    for (uid, brief_id), usd in usd_by_uid_brief.items():
        usd_by_uid[uid] = usd_by_uid.get(uid, 0.0) + usd * scale[brief_id]
    return usd_by_uid


def compute_weights(
    usd_by_uid: Dict[int, float],
    uids: List[int],
    total_daily_usd: float,
    burn_uid: int = 0,
) -> np.ndarray:
    weights = np.zeros(len(uids), dtype=np.float32)
    pos = {uid: i for i, uid in enumerate(uids)}

    total = sum(max(0.0, v) for v in usd_by_uid.values())
    denom = max(total, total_daily_usd)
    if denom <= 0:
        return weights

    for uid, usd in usd_by_uid.items():
        if uid in pos and usd > 0:
            weights[pos[uid]] = usd / denom

    burn = max(total_daily_usd - total, 0.0)
    if burn > 0 and burn_uid in pos:
        weights[pos[burn_uid]] += burn / denom

    return weights
