"""Turn each epoch's payable installments into its weight vector over the miners' own UIDs."""

import math
from typing import Dict, List, Tuple

import bittensor as bt
import numpy as np

from herald.base.utils.weight_utils import convert_weights_and_uids_for_emit

# UID 0 receives, and so burns, the share of the day's miner emission that verified value does not
# cover, and all of it on a day that is not scored.
BURN_UID = 0


class WeightVectorRefused(Exception):
    """The weight vector breaks the submission rules and must not be sent to the chain."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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

    Order-independent per-brief scaling ensures every validator computes the same payable totals
    from the same signed briefs and persisted state.
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


def miner_weight_vector(usd_by_uid: Dict[int, float], daily_usd) -> Tuple[List[int], np.ndarray]:
    """The epoch's weight vector: each miner UID's payable USD over the day's miner emission in USD.

    With U the total payable and d the USD value of the day's miner emission: when U <= d each miner
    UID receives usd / d and UID 0 the rest, 1 - U / d, which is burned; when U > d the miners share all
    of it, usd / U each, and UID 0 receives nothing. Everything goes to UID 0 ([0], [1.0]) when nothing
    is payable or d is not a finite positive number. UID 0 itself and non-positive or non-finite
    amounts never count as a miner's pay. UIDs come out in ascending order.
    """
    burn = ([BURN_UID], np.array([1.0], dtype=np.float32))
    daily = float(daily_usd)
    paid = {}
    for uid, usd in usd_by_uid.items():
        uid, usd = int(uid), float(usd)
        if uid > BURN_UID and math.isfinite(usd) and usd > 0:
            paid[uid] = usd
    if not paid or not math.isfinite(daily) or daily <= 0:
        return burn
    total = math.fsum(paid[uid] for uid in sorted(paid))
    denominator = max(total, daily)
    pairs = [(uid, paid[uid] / denominator) for uid in sorted(paid)]
    if total < daily:
        pairs.insert(0, (BURN_UID, (daily - total) / daily))
    return [uid for uid, _ in pairs], np.array([share for _, share in pairs], dtype=np.float32)


def allowed_emit_vector(scores, hotkeys, weight_hotkeys: Dict[int, str],
                        min_allowed_weights) -> Tuple[List[int], List[int]]:
    """The u16 (uids, weights) to submit, or WeightVectorRefused.

    UID 0 and the miner UIDs the latest epoch was scored on may receive weight. `weight_hotkeys` maps
    each of those UIDs to the hotkey it held when the epoch was scored. A positive score on any other
    UID, or on a UID now held by a different hotkey (the miner was deregistered and someone else took
    the UID), moves to UID 0 and is logged, so a miner's pay never reaches another account. Weights are
    max-upscaled to u16 with zero entries dropped; they are never padded with other UIDs or clipped to
    a maximum weight. The vector is refused when it is empty, reaches a UID that is not allowed, or is
    shorter than MinAllowedWeights (refused as well when MinAllowedWeights is unknown).
    """
    values = np.asarray(scores, dtype=np.float64).reshape(-1)
    hotkeys = list(hotkeys)
    scored = {int(uid): hotkey for uid, hotkey in (weight_hotkeys or {}).items()}
    support = [int(uid) for uid in np.flatnonzero(np.isfinite(values) & (values > 0))]
    if not support:
        bt.logging.warning("WEIGHT_VECTOR_BURN reason=no_scores")
        support_uids, shares = [BURN_UID], np.array([1.0])
    else:
        moved = [uid for uid in support if uid != BURN_UID
                 and (uid >= len(hotkeys) or scored.get(uid) is None or scored[uid] != hotkeys[uid])]
        total = math.fsum(values[support])
        share_by_uid = {uid: values[uid] / total for uid in support if uid not in moved}
        if moved:
            bt.logging.warning(
                f"WEIGHT_VECTOR_BURN reason=hotkey_changed: scores on UIDs {moved[:10]} are not held by "
                f"the hotkeys the epoch was scored for; their weight moves to UID {BURN_UID}"
            )
            share_by_uid[BURN_UID] = share_by_uid.get(BURN_UID, 0.0) + math.fsum(values[moved]) / total
        support_uids = sorted(share_by_uid)
        shares = np.array([share_by_uid[uid] for uid in support_uids])
    allowed = {BURN_UID} | {uid for uid in support_uids if uid in scored}
    uids, weights = convert_weights_and_uids_for_emit(
        uids=np.asarray(support_uids, dtype=np.int64), weights=shares,
    )
    uids = [int(uid) for uid in uids]
    weights = [int(weight) for weight in weights]
    if not uids:
        raise WeightVectorRefused("empty_vector")
    if not set(uids) <= allowed:
        raise WeightVectorRefused(f"uid_not_allowed uids={uids} allowed={sorted(allowed)}")
    if min_allowed_weights is None:
        raise WeightVectorRefused("min_allowed_weights_unknown")
    if len(uids) < int(min_allowed_weights):
        raise WeightVectorRefused(
            f"below_min_allowed_weights uids={uids} min_allowed_weights={int(min_allowed_weights)}"
        )
    return uids, weights
