import math

import numpy as np
import pytest

from herald.validator.news import emission
from herald.validator.news.emission import (
    BURN_UID,
    WeightVectorRefused,
    allowed_emit_vector,
    apply_reward_pools,
    miner_weight_vector,
)
from herald.validator.news.vesting import VestingLedger


def test_overlapping_30_day_installments_aggregate_by_uid():
    briefs = [
        {"id": "miner1-placement", "kind": "standing"},
        {"id": "miner2-placement", "kind": "standing"},
        {"id": "miner3-day5", "kind": "standing"},
        {"id": "miner3-day1", "kind": "standing"},
    ]
    daily_usd = apply_reward_pools(
        {
            (1, "miner1-placement"): 500.0 / 30,
            (2, "miner2-placement"): 700.0 / 30,
            (3, "miner3-day5"): 500.0 / 30,
            (3, "miner3-day1"): 500.0 / 30,
        },
        briefs,
        pool_spent={},
    )

    assert daily_usd == {
        1: pytest.approx(500.0 / 30),
        2: pytest.approx(700.0 / 30),
        3: pytest.approx((500.0 / 30) * 2),
    }


def test_day_five_and_day_one_placements_contribute_together():
    vesting = VestingLedger(vest_epochs=30)
    vesting.start("older", uid=3, total_usd=500.0, brief_id="old", start_epoch=1)
    for epoch in range(1, 5):
        assert vesting.release("older", epoch) == pytest.approx(500.0 / 30)

    vesting.start("newer", uid=3, total_usd=500.0, brief_id="new", start_epoch=5)
    daily_usd = apply_reward_pools(
        {
            (3, "old"): vesting.release("older", 5),
            (3, "new"): vesting.release("newer", 5),
        },
        [{"id": "old", "kind": "standing"}, {"id": "new", "kind": "standing"}],
        pool_spent={},
    )

    assert daily_usd[3] == pytest.approx((500.0 / 30) * 2)
    assert vesting.entry("older").remaining == 25
    assert vesting.entry("newer").remaining == 29


def test_client_brief_under_pool_unchanged():
    spent = {}
    usd = apply_reward_pools(
        {(1, "c1"): 300.0},
        [{"id": "c1", "kind": "client", "reward_pool": 500.0}],
        pool_spent=spent,
    )

    assert usd == {1: 300.0}
    assert spent["c1"] == pytest.approx(300.0)


def test_client_brief_over_pool_scaled_down_proportionally():
    spent = {}
    usd = apply_reward_pools(
        {(1, "c1"): 300.0, (2, "c1"): 100.0},
        [{"id": "c1", "kind": "client", "reward_pool": 200.0}],
        pool_spent=spent,
    )

    assert usd[1] == pytest.approx(150.0)
    assert usd[2] == pytest.approx(50.0)
    assert spent["c1"] == pytest.approx(200.0)


def test_client_pool_drawn_down_across_epochs():
    briefs = [{"id": "c1", "kind": "client", "reward_pool": 100.0}]
    spent = {}

    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, spent)[1] == pytest.approx(80.0)
    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, spent)[1] == pytest.approx(20.0)
    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, spent).get(1, 0.0) == 0.0
    assert spent["c1"] == pytest.approx(100.0)


def test_unfunded_client_brief_pays_zero():
    usd = apply_reward_pools(
        {(1, "c1"): 500.0},
        [{"id": "c1", "kind": "client"}],
        pool_spent={},
    )

    assert usd.get(1, 0.0) == 0.0


def test_standing_installments_are_not_capped_by_client_draw():
    spent = {}
    usd = apply_reward_pools(
        {(1, "c1"): 300.0, (2, "s1"): 900.0},
        [
            {"id": "c1", "kind": "client", "reward_pool": 300.0},
            {"id": "s1", "kind": "standing"},
        ],
        pool_spent=spent,
    )

    assert usd[1] == pytest.approx(300.0)
    assert usd[2] == pytest.approx(900.0)
    assert spent["c1"] == pytest.approx(300.0)


def test_reward_pools_are_order_independent():
    briefs = [
        {"id": "a", "kind": "client", "reward_pool": 100.0},
        {"id": "b", "kind": "client", "reward_pool": 100.0},
    ]
    forward = apply_reward_pools(
        {(1, "a"): 300.0, (2, "b"): 300.0}, briefs, pool_spent={}
    )
    reverse = apply_reward_pools(
        {(2, "b"): 300.0, (1, "a"): 300.0}, briefs, pool_spent={}
    )

    assert forward == reverse
    assert forward == {1: pytest.approx(100.0), 2: pytest.approx(100.0)}


def test_overlapping_participant_aggregation_is_order_independent():
    briefs = [
        {"id": "a", "kind": "standing"},
        {"id": "b", "kind": "standing"},
        {"id": "c", "kind": "standing"},
    ]
    forward = apply_reward_pools(
        {(3, "a"): 1e16, (3, "b"): 1.0, (2, "c"): 5.0}, briefs, pool_spent={}
    )
    reverse = apply_reward_pools(
        {(2, "c"): 5.0, (3, "b"): 1.0, (3, "a"): 1e16}, briefs, pool_spent={}
    )

    assert forward == reverse


def test_client_briefs_are_pool_capped_but_not_globally_daily_capped():
    briefs = [
        {"id": "a", "kind": "client", "reward_pool": 5000.0},
        {"id": "b", "kind": "client", "reward_pool": 5000.0},
        {"id": "s1", "kind": "standing"},
    ]
    spent = {}
    usd = apply_reward_pools(
        {(1, "a"): 900.0, (2, "b"): 600.0, (3, "s1"): 100.0},
        briefs,
        pool_spent=spent,
    )

    assert usd == {1: pytest.approx(900.0), 2: pytest.approx(600.0), 3: pytest.approx(100.0)}
    assert spent["a"] == pytest.approx(900.0)
    assert spent["b"] == pytest.approx(600.0)


def test_live_canary_overlap_pool_clipping_and_next_epoch_exhaustion():
    """Pin the netuid-535 canary economics before exposing its claims to the validator.

    Miner 1 has an older standing placement entering day 3. Miners 2 and 3 enter day 1 of
    one client brief whose $20 pool is smaller than their combined $26.67 installment. The pool
    clips those two proportionally, and the exhausted client brief contributes nothing on the next
    epoch while the standing placement continues.
    """
    vesting = VestingLedger(vest_epochs=30)
    vesting.start("standing-old", uid=1, total_usd=500.0, brief_id="b_news", start_epoch=1050)
    assert vesting.release("standing-old", 1050) == pytest.approx(500.0 / 30)
    assert vesting.release("standing-old", 1051) == pytest.approx(500.0 / 30)

    vesting.start("client-t1", uid=2, total_usd=500.0, brief_id="client", start_epoch=1052)
    vesting.start("client-t2", uid=3, total_usd=300.0, brief_id="client", start_epoch=1052)
    briefs = [
        {"id": "b_news", "kind": "standing"},
        {"id": "client", "kind": "client", "reward_pool": 20.0},
    ]
    spent = {}

    paid_1052 = apply_reward_pools(
        {
            (1, "b_news"): vesting.release("standing-old", 1052),
            (2, "client"): vesting.release("client-t1", 1052),
            (3, "client"): vesting.release("client-t2", 1052),
        },
        briefs,
        spent,
    )

    assert paid_1052 == {
        1: pytest.approx(500.0 / 30),
        2: pytest.approx(12.5),
        3: pytest.approx(7.5),
    }
    assert spent["client"] == pytest.approx(20.0)

    paid_1053 = apply_reward_pools(
        {
            (1, "b_news"): vesting.release("standing-old", 1053),
            (2, "client"): vesting.release("client-t1", 1053),
            (3, "client"): vesting.release("client-t2", 1053),
        },
        briefs,
        spent,
    )

    assert paid_1053 == {1: pytest.approx(500.0 / 30)}
    assert spent["client"] == pytest.approx(20.0)


# --- weight vectors ---------------------------------------------------------------------------------



def as_lists(vector):
    uids, weights = vector
    assert weights.dtype == np.float32
    return list(uids), [float(weight) for weight in weights]


def scores_on(shares, n=256):
    scores = np.zeros(n, dtype=np.float32)
    for uid, share in shares.items():
        scores[uid] = share
    return scores


# ── The weight rule: each miner UID's verified USD over the day's miner emission ─────────────────

def test_each_miner_receives_its_usd_over_the_daily_value_and_uid_zero_the_rest():
    uids, weights = as_lists(miner_weight_vector({7: 250.0, 3: 150.0}, 1000.0))
    assert (uids, weights) == ([0, 3, 7], pytest.approx([0.6, 0.15, 0.25]))


def test_a_single_miner_below_the_daily_value_splits_with_uid_zero():
    assert as_lists(miner_weight_vector({12: 500.0}, 1000.0)) == ([0, 12], [0.5, 0.5])


@pytest.mark.parametrize("usd", [{4: 1000.0}, {4: 600.0, 9: 400.0}])
def test_miners_paying_exactly_the_daily_value_leave_nothing_to_burn(usd):
    uids, weights = as_lists(miner_weight_vector(usd, 1000.0))
    assert BURN_UID not in uids
    assert math.fsum(weights) == pytest.approx(1.0)


def test_miners_above_the_daily_value_share_all_of_it_in_proportion():
    assert as_lists(miner_weight_vector({2: 3000.0, 5: 1000.0}, 1000.0)) == ([2, 5], [0.75, 0.25])


@pytest.mark.parametrize("usd", [{}, {4: 0.0}, {4: -5.0}, {4: math.nan}, {4: math.inf},
                                 {BURN_UID: 400.0}, {-1: 400.0}])
def test_nothing_payable_to_a_miner_uid_burns_everything(usd):
    assert as_lists(miner_weight_vector(usd, 1000.0)) == ([BURN_UID], [1.0])


@pytest.mark.parametrize("daily", [0.0, -1.0, math.nan, math.inf])
def test_an_unusable_daily_value_burns_everything(daily):
    assert as_lists(miner_weight_vector({4: 250.0}, daily)) == ([BURN_UID], [1.0])


def test_the_vector_is_independent_of_input_order():
    one = as_lists(miner_weight_vector({9: 100.0, 2: 200.0, 5: 300.0}, 1000.0))
    two = as_lists(miner_weight_vector({5: 300.0, 9: 100.0, 2: 200.0}, 1000.0))
    assert one == two and one[0] == [0, 2, 5, 9]


# ── The submission guard: only UID 0 and the miner UIDs the epoch was scored for ─────────────────

HOTKEYS = [f"hk{uid}" for uid in range(12)]
SCORED = {3: "hk3", 7: "hk7"}


@pytest.mark.parametrize("min_allowed", [0, 1, 3, 4])
def test_scores_on_uid_zero_and_the_scored_miners_are_submitted(min_allowed):
    scores = scores_on({0: 0.6, 3: 0.25, 7: 0.15}, n=len(HOTKEYS))
    if min_allowed > 3:
        with pytest.raises(WeightVectorRefused, match="below_min_allowed_weights"):
            allowed_emit_vector(scores, HOTKEYS, SCORED, min_allowed)
        return
    uids, weights = allowed_emit_vector(scores, HOTKEYS, SCORED, min_allowed)
    # Max-upscaled, never padded or clipped: 0.25 / 0.6 * 65535 = 27306.25; 0.15 / 0.6 = 16383.75.
    assert (uids, weights) == ([0, 3, 7], [65535, 27306, 16384])


@pytest.mark.parametrize("min_allowed", [0, 1, 2])
def test_burn_only_scores_are_never_padded(min_allowed):
    scores = scores_on({0: 1.0}, n=len(HOTKEYS))
    if min_allowed > 1:
        with pytest.raises(WeightVectorRefused, match="below_min_allowed_weights"):
            allowed_emit_vector(scores, HOTKEYS, SCORED, min_allowed)
        return
    assert allowed_emit_vector(scores, HOTKEYS, SCORED, min_allowed) == ([0], [65535])


def test_a_score_on_a_uid_the_epoch_did_not_score_moves_to_uid_zero():
    scores = scores_on({0: 0.5, 3: 0.25, 9: 0.25}, n=len(HOTKEYS))
    # UID 9 was never scored: its quarter joins UID 0's half; UID 3 keeps its quarter.
    assert allowed_emit_vector(scores, HOTKEYS, SCORED, 1) == ([0, 3], [65535, 21845])


def test_a_scored_uid_another_hotkey_has_taken_moves_to_uid_zero_and_the_others_stay():
    hotkeys = list(HOTKEYS)
    hotkeys[7] = "hk-new-registrant"
    scores = scores_on({0: 0.6, 3: 0.25, 7: 0.15}, n=len(hotkeys))
    assert allowed_emit_vector(scores, hotkeys, SCORED, 1) == ([0, 3], [65535, 21845])


def test_a_scored_uid_beyond_the_metagraph_moves_to_uid_zero():
    scores = scores_on({0: 0.5, 3: 0.5}, n=len(HOTKEYS))
    assert allowed_emit_vector(scores, HOTKEYS[:3], SCORED, 1) == ([0], [65535])


def test_with_nothing_scored_every_miner_score_moves_to_uid_zero():
    scores = scores_on({3: 0.5, 7: 0.5}, n=len(HOTKEYS))
    assert allowed_emit_vector(scores, HOTKEYS, {}, 1) == ([0], [65535])


def test_zero_or_non_finite_scores_burn():
    assert allowed_emit_vector(np.zeros(len(HOTKEYS)), HOTKEYS, SCORED, 1) == ([0], [65535])
    scores = scores_on({3: 1.0}, n=len(HOTKEYS))
    scores[5] = np.nan
    assert allowed_emit_vector(scores, HOTKEYS, SCORED, 1) == ([3], [65535])


def test_unknown_min_allowed_weights_is_refused():
    with pytest.raises(WeightVectorRefused, match="min_allowed_weights_unknown"):
        allowed_emit_vector(scores_on({0: 1.0}, n=len(HOTKEYS)), HOTKEYS, SCORED, None)


@pytest.mark.parametrize("converted, reason", [
    (([0, 5], [65535, 100]), "uid_not_allowed"),
    (([], []), "empty_vector"),
])
def test_emitted_uids_are_checked_after_conversion(monkeypatch, converted, reason):
    monkeypatch.setattr(emission, "convert_weights_and_uids_for_emit", lambda uids, weights: converted)
    with pytest.raises(WeightVectorRefused, match=reason):
        allowed_emit_vector(scores_on({0: 0.9, 3: 0.1}, n=len(HOTKEYS)), HOTKEYS, SCORED, 0)
