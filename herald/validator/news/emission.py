"""Turn each epoch's payable installments into the incentive and burn weight vector."""

import math
from typing import Dict, List, Tuple

import bittensor as bt
import numpy as np

from herald.base.utils.weight_utils import convert_weights_and_uids_for_emit

# UID 0 receives the share of the day's miner emission that verified value does not cover.
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


def incentive_uid(hotkeys, incentive_hotkey: str):
    """The incentive hotkey's UID, or None when it is unset, unregistered or holds UID 0."""
    hotkeys = list(hotkeys)
    if not incentive_hotkey or incentive_hotkey not in hotkeys:
        return None
    uid = hotkeys.index(incentive_hotkey)
    return None if uid == BURN_UID else uid


def incentive_burn_vector(payable_usd, daily_usd, uid_star) -> Tuple[List[int], np.ndarray]:
    """([0, uid_star], [1 - w, w]) with w = min(1, payable_usd / daily_usd); zero entries dropped.

    Everything goes to UID 0 ([0], [1.0]) when there is no incentive UID, nothing is payable, or the
    USD value of the day's miner emission is not a finite positive number.
    """
    burn = ([BURN_UID], np.array([1.0], dtype=np.float32))
    if uid_star is None or int(uid_star) <= BURN_UID:
        return burn
    payable = float(payable_usd)
    daily = float(daily_usd)
    if not math.isfinite(payable) or payable <= 0 or not math.isfinite(daily) or daily <= 0:
        return burn
    w = min(1.0, payable / daily)
    pairs = [(uid, share) for uid, share in ((BURN_UID, 1.0 - w), (int(uid_star), w)) if share > 0]
    return [uid for uid, _ in pairs], np.array([share for _, share in pairs], dtype=np.float32)


def allowed_emit_vector(scores, hotkeys, incentive_hotkey: str,
                        min_allowed_weights) -> Tuple[List[int], List[int]]:
    """The u16 (uids, weights) to submit, or WeightVectorRefused.

    Only UID 0 and the incentive hotkey's current UID may receive weight. Scores on any other UID
    (for example after the incentive hotkey moved to a new UID) put all weight on UID 0. Weights are
    max-upscaled to u16 with zero entries dropped; they are never padded with other UIDs or clipped
    to a maximum weight. The vector is refused when it is empty, reaches another UID, or is shorter
    than MinAllowedWeights (refused as well when MinAllowedWeights is unknown).
    """
    values = np.asarray(scores, dtype=np.float64).reshape(-1)
    uid_star = incentive_uid(hotkeys, incentive_hotkey)
    allowed = {BURN_UID} if uid_star is None else {BURN_UID, uid_star}
    support = [int(uid) for uid in np.flatnonzero(np.isfinite(values) & (values > 0))]
    if not support or not set(support) <= allowed:
        bt.logging.warning(
            f"WEIGHT_VECTOR_BURN reason=incentive_uid_changed: scores on UIDs {support[:10]} are not "
            f"limited to UID {BURN_UID} and the incentive hotkey's UID {uid_star}"
        )
        support_uids, shares = [BURN_UID], np.array([1.0])
    else:
        support_uids = support
        shares = values[support] / math.fsum(values[support])
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
