"""Convert each epoch's payable placement installments into participant weights."""

import math
from typing import Dict, List, Tuple

import numpy as np


def apply_reward_pools(
    usd_by_uid_brief: Dict[Tuple[int, str], float],
    briefs: List[dict],
    pool_spent: Dict[str, float],
) -> Dict[int, float]:
    """Apply per-brief funding limits, then aggregate payable installments by UID.

    Client briefs (``kind != "standing"``) are paid from their prepaid ``reward_pool``, drawn down
    across epochs via ``pool_spent`` so total pay never exceeds the pool; an unfunded brief (no pool
    left) pays nothing. Standing placements contribute their full installment value. ``pool_spent``
    is mutated with this epoch's actual client payouts.

    Order-independent per-brief scaling ensures every validator computes the same participant
    proportions from the same signed briefs and persisted state.
    """
    usd_by_uid: Dict[int, float] = {}
    by_id = {b["id"]: b for b in briefs}
    values_by_brief: Dict[str, List[float]] = {}
    for (_, brief_id), usd in usd_by_uid_brief.items():
        values_by_brief.setdefault(brief_id, []).append(max(0.0, usd))
    brief_totals = {
        brief_id: math.fsum(sorted(values))
        for brief_id, values in values_by_brief.items()
    }

    scale: Dict[str, float] = {}
    for brief_id in sorted(brief_totals):
        total = brief_totals[brief_id]
        brief = by_id.get(brief_id, {})
        if brief.get("kind") == "standing":
            scale[brief_id] = 1.0
            continue
        remaining = max(0.0, float(brief.get("reward_pool", 0.0)) - pool_spent.get(brief_id, 0.0))
        paid = min(total, remaining)
        scale[brief_id] = paid / total if total > 0 else 0.0
        pool_spent[brief_id] = pool_spent.get(brief_id, 0.0) + paid

    paid_by_uid: Dict[int, List[float]] = {}
    for (uid, brief_id), usd in usd_by_uid_brief.items():
        paid = max(0.0, usd) * scale.get(brief_id, 0.0)
        if paid > 0:
            paid_by_uid.setdefault(uid, []).append(paid)
    for uid in sorted(paid_by_uid):
        usd_by_uid[uid] = math.fsum(sorted(paid_by_uid[uid]))
    return usd_by_uid


def compute_weights(
    usd_by_uid: Dict[int, float],
    uids: List[int],
) -> np.ndarray:
    weights = np.zeros(len(uids), dtype=np.float32)
    pos = {uid: i for i, uid in enumerate(uids)}

    payable = {uid: max(0.0, usd) for uid, usd in usd_by_uid.items() if uid in pos}
    total = math.fsum(payable[uid] for uid in sorted(payable))
    if total <= 0:
        return weights

    for uid in sorted(payable):
        usd = payable[uid]
        if usd > 0:
            weights[pos[uid]] = usd / total

    return weights
