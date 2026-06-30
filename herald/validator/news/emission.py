"""Convert per-UID USD into a weight vector, burning the unclaimed remainder."""

from typing import Dict, List

import numpy as np


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
