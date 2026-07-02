"""Convert per-UID USD into a weight vector, burning the unclaimed remainder."""

from typing import Dict, List, Tuple

import numpy as np


def apply_reward_pools(
    usd_by_uid_brief: Dict[Tuple[int, str], float],
    briefs: List[dict],
    total_daily_usd: float,
    pool_spent: Dict[str, float],
) -> Dict[int, float]:
    """Partition the daily emission budget across briefs, then flatten to per-UID USD.

    Client briefs (``kind != "standing"``) are paid from their prepaid ``reward_pool``, drawn down
    across epochs via ``pool_spent`` so total pay never exceeds the pool; an unfunded brief (no pool
    left) pays nothing. The aggregate client draw is additionally capped at ``total_daily_usd``
    (scaled down proportionally), so concurrent pools can never push total pay past the daily budget
    — which would zero both the standing budget and the burn. The standing brief(s)
    (``kind == "standing"``) take the remainder of the daily budget after client draw.
    ``pool_spent`` is mutated with this epoch's actual (post-cap) client payouts.

    Order-independent (per-brief pool caps, then one aggregate scale over their sum), so every
    validator computes the same split from the same signed briefs + state.
    """
    usd_by_uid: Dict[int, float] = {}
    if total_daily_usd <= 0:
        for (uid, _), usd in usd_by_uid_brief.items():
            usd_by_uid[uid] = usd_by_uid.get(uid, 0.0) + usd
        return usd_by_uid

    by_id = {b["id"]: b for b in briefs}
    brief_totals: Dict[str, float] = {}
    for (_, brief_id), usd in usd_by_uid_brief.items():
        brief_totals[brief_id] = brief_totals.get(brief_id, 0.0) + usd

    scale: Dict[str, float] = {}
    standing_ids: List[str] = []
    paid_by_brief: Dict[str, float] = {}
    for brief_id, total in brief_totals.items():
        brief = by_id.get(brief_id, {})
        if brief.get("kind") == "standing":
            standing_ids.append(brief_id)
            continue
        remaining = max(0.0, float(brief.get("reward_pool", 0.0)) - pool_spent.get(brief_id, 0.0))
        paid_by_brief[brief_id] = min(total, remaining)

    total_client = sum(paid_by_brief.values())
    budget_scale = min(1.0, total_daily_usd / total_client) if total_client > 0 else 1.0
    client_draw = 0.0
    for brief_id, paid in paid_by_brief.items():
        paid *= budget_scale
        total = brief_totals[brief_id]
        scale[brief_id] = paid / total if total > 0 else 0.0
        pool_spent[brief_id] = pool_spent.get(brief_id, 0.0) + paid
        client_draw += paid

    standing_budget = max(0.0, total_daily_usd - client_draw)
    standing_total = sum(brief_totals[bid] for bid in standing_ids)
    standing_scale = min(1.0, standing_budget / standing_total) if standing_total > 0 else 0.0
    for bid in standing_ids:
        scale[bid] = standing_scale

    for (uid, brief_id), usd in usd_by_uid_brief.items():
        usd_by_uid[uid] = usd_by_uid.get(uid, 0.0) + usd * scale.get(brief_id, 0.0)
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
