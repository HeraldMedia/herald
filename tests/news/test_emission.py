import math

import numpy as np
import pytest

from herald.validator.news import emission
from herald.validator.news.emission import (
    BURN_UID,
    SHARE_PPB,
    WeightVectorRefused,
    allowed_emit_vector,
    apply_reward_pools,
    contributor_share_ppb,
    full_incentive_vector,
    incentive_burn_vector,
    incentive_uid,
    incentive_weight_vector,
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


# --- incentive and burn vector ----------------------------------------------------------------------

UID_STAR = 7
HOTKEYS = [f"hk{uid}" for uid in range(256)]
STAR = HOTKEYS[UID_STAR]


def as_lists(vector):
    uids, weights = vector
    assert weights.dtype == np.float32
    return list(uids), [float(weight) for weight in weights]


def scores_on(shares, n=256):
    scores = np.zeros(n, dtype=np.float32)
    for uid, share in shares.items():
        scores[uid] = share
    return scores


@pytest.mark.parametrize("payable", [0.0, -5.0, math.nan])
def test_nothing_payable_burns_everything(payable):
    assert as_lists(incentive_burn_vector(payable, 1000.0, UID_STAR)) == ([BURN_UID], [1.0])


def test_payable_half_the_daily_value_splits_the_vector():
    assert as_lists(incentive_burn_vector(500.0, 1000.0, UID_STAR)) == ([0, UID_STAR], [0.5, 0.5])


def test_share_is_payable_over_the_daily_value():
    uids, weights = as_lists(incentive_burn_vector(250.0, 1000.0, UID_STAR))
    assert uids == [0, UID_STAR] and weights == pytest.approx([0.75, 0.25])


@pytest.mark.parametrize("payable", [1000.0, 5000.0])
def test_payable_at_or_above_the_daily_value_pays_only_the_incentive_uid(payable):
    assert as_lists(incentive_burn_vector(payable, 1000.0, UID_STAR)) == ([UID_STAR], [1.0])


@pytest.mark.parametrize("uid_star", [None, 0])
def test_no_incentive_uid_burns_everything(uid_star):
    assert as_lists(incentive_burn_vector(500.0, 1000.0, uid_star)) == ([BURN_UID], [1.0])


@pytest.mark.parametrize("daily", [0.0, -1.0, math.nan, math.inf])
def test_unusable_daily_value_burns_everything(daily):
    assert as_lists(incentive_burn_vector(500.0, daily, UID_STAR)) == ([BURN_UID], [1.0])


# --- the burn setting: both modes' vectors and the contributors' share ------------------------------

PAYABLE_DAILY = [
    (0.0, 1000.0), (-5.0, 1000.0), (math.nan, 1000.0), (math.inf, 1000.0), (250.0, 1000.0),
    (500.0 / 30, 1234.5678), (1000.0 / 3, 1000.0), (999.999, 1000.0), (1000.0, 1000.0),
    (5000.0, 1000.0), (500.0, 0.0), (500.0, -1.0), (500.0, math.nan), (500.0, math.inf),
]


@pytest.mark.parametrize("payable, daily", PAYABLE_DAILY)
@pytest.mark.parametrize("uid_star", [UID_STAR, 1, 255, None, 0])
def test_with_the_burn_the_vector_is_exactly_the_incentive_and_burn_vector(payable, daily, uid_star):
    uids, weights = incentive_weight_vector(payable, daily, uid_star, True)
    expected_uids, expected_weights = incentive_burn_vector(payable, daily, uid_star)
    assert uids == expected_uids
    assert weights.dtype == expected_weights.dtype == np.float32
    assert weights.tobytes() == expected_weights.tobytes()


@pytest.mark.parametrize("payable, daily, vector", [
    (0.0, 1000.0, ([BURN_UID], [1.0])),
    (250.0, 1000.0, ([0, UID_STAR], [0.75, 0.25])),
    (500.0, 1000.0, ([0, UID_STAR], [0.5, 0.5])),
    (100.0, 300.0, ([0, UID_STAR], [float(np.float32(1 - 1 / 3)), float(np.float32(1 / 3))])),
    (1000.0, 1000.0, ([UID_STAR], [1.0])),
    (5000.0, 1000.0, ([UID_STAR], [1.0])),
    (500.0, math.nan, ([BURN_UID], [1.0])),
])
def test_with_the_burn_the_vectors_are_unchanged(payable, daily, vector):
    assert as_lists(incentive_weight_vector(payable, daily, UID_STAR, True)) == vector


@pytest.mark.parametrize("payable, daily", PAYABLE_DAILY)
def test_without_the_burn_the_incentive_uid_receives_all_the_weight(payable, daily):
    # Whatever was verified, priced or payable: the incentive UID's receipt does not depend on it.
    assert as_lists(incentive_weight_vector(payable, daily, UID_STAR, False)) == ([UID_STAR], [1.0])


@pytest.mark.parametrize("burn_unearned", [False, True])
@pytest.mark.parametrize("uid_star", [None, 0])
def test_without_an_incentive_uid_both_modes_burn_everything(burn_unearned, uid_star):
    assert as_lists(incentive_weight_vector(5000.0, 1000.0, uid_star, burn_unearned)) == (
        [BURN_UID], [1.0])


def test_the_full_incentive_vector_is_a_single_entry():
    assert as_lists(full_incentive_vector(UID_STAR)) == ([UID_STAR], [1.0])
    assert as_lists(full_incentive_vector(np.int64(3))) == ([3], [1.0])
    assert as_lists(full_incentive_vector(None)) == ([BURN_UID], [1.0])
    assert as_lists(full_incentive_vector(0)) == ([BURN_UID], [1.0])


@pytest.mark.parametrize("payable, daily", PAYABLE_DAILY)
def test_with_the_burn_all_of_the_receipt_is_owed_to_contributors(payable, daily):
    # The chain already scaled the incentive UID's receipt to verified value.
    assert contributor_share_ppb(payable, daily, True) == SHARE_PPB == 1_000_000_000


@pytest.mark.parametrize("payable, daily, share", [
    (250.0, 1000.0, 250_000_000),
    (500.0, 1000.0, 500_000_000),
    (100.0, 1000.0, 100_000_000),
    (1000.0 / 3, 1000.0, 333_333_333),
    (2000.0 / 3, 1000.0, 666_666_666),
    (1.0, 1e9, 1),
    (1.0, 2e9, 0),  # half a part per billion rounds down
    # The exact floor of the two values held: 0.3 is 0.29999999999999998889... as a float, so a
    # rounded float product (300000000.0) would overstate it.
    (0.3, 1.0, 299_999_999),
    (999.999, 1000.0, 999_999_000),
    (1000.0, 1000.0, 1_000_000_000),
    (5000.0, 1000.0, 1_000_000_000),
    (math.inf, 1000.0, 0),
])
def test_without_the_burn_the_share_is_the_floor_of_payable_over_daily_capped_at_1e9(payable, daily,
                                                                                    share):
    result = contributor_share_ppb(payable, daily, False)
    assert result == share and type(result) is int
    assert 0 <= result <= 1_000_000_000


@pytest.mark.parametrize("payable, daily", [
    (0.0, 1000.0), (-5.0, 1000.0), (math.nan, 1000.0),
    (500.0, 0.0), (500.0, -1.0), (500.0, math.nan), (500.0, math.inf),
])
def test_without_the_burn_nothing_payable_or_an_unusable_daily_value_owes_nothing(payable, daily):
    assert contributor_share_ppb(payable, daily, False) == 0


def test_the_share_owes_contributors_what_the_burn_would_have_paid_them():
    # Burn on: the receipt is w of the day's miner emission, all owed. Burn off: the receipt is
    # the whole emission and the share owed is w, to within one part per billion.
    for payable, daily in ((250.0, 1000.0), (500.0 / 30, 1234.5678), (999.999, 1000.0)):
        [w] = [float(weight) for uid, weight in zip(*incentive_burn_vector(payable, daily, UID_STAR))
               if uid == UID_STAR]
        owed_with_burn = w * contributor_share_ppb(payable, daily, True) / 1e9
        owed_without = 1.0 * contributor_share_ppb(payable, daily, False) / 1e9
        assert owed_without == pytest.approx(owed_with_burn, rel=1e-6, abs=1e-9)


def test_incentive_uid_resolves_only_a_registered_hotkey_above_uid_zero():
    assert incentive_uid(HOTKEYS, STAR) == UID_STAR
    assert incentive_uid(HOTKEYS, "unregistered") is None
    assert incentive_uid(HOTKEYS, "") is None
    assert incentive_uid(HOTKEYS, HOTKEYS[0]) is None


@pytest.mark.parametrize("min_allowed", [0, 1, 2, 3])
def test_incentive_and_burn_scores_reach_only_their_two_uids(min_allowed):
    scores = scores_on({0: 0.9, UID_STAR: 0.1})
    if min_allowed > 2:
        with pytest.raises(WeightVectorRefused, match="below_min_allowed_weights"):
            allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed)
        return
    uids, weights = allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed)
    assert set(uids) <= {0, UID_STAR}
    # Max-upscaled, not clipped to a maximum weight: 0.1 / 0.9 * 65535 = 7281.7.
    assert (uids, weights) == ([0, UID_STAR], [65535, 7282])


@pytest.mark.parametrize("min_allowed", [0, 1, 2, 3])
def test_burn_only_scores_are_never_padded(min_allowed):
    scores = scores_on({0: 1.0})
    if min_allowed > 1:
        with pytest.raises(WeightVectorRefused, match="below_min_allowed_weights"):
            allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed)
        return
    assert allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed) == ([0], [65535])


@pytest.mark.parametrize("min_allowed", [0, 1, 2])
def test_full_incentive_weight_is_a_single_entry(min_allowed):
    scores = scores_on({UID_STAR: 0.3})
    if min_allowed > 1:
        with pytest.raises(WeightVectorRefused):
            allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed)
        return
    assert allowed_emit_vector(scores, HOTKEYS, STAR, min_allowed) == ([UID_STAR], [65535])


@pytest.mark.parametrize("shares", [
    {3: 0.5, 157: 0.5},
    {0: 0.9, UID_STAR: 0.1, 3: 0.01},
])
def test_scores_on_any_other_uid_burn(shares):
    assert allowed_emit_vector(scores_on(shares), HOTKEYS, STAR, 1) == ([0], [65535])


def test_incentive_hotkey_moved_to_a_new_uid_burns_scores_left_on_the_old_one():
    hotkeys = list(HOTKEYS)
    hotkeys[UID_STAR] = "hk-replacement"
    hotkeys[9] = STAR
    assert allowed_emit_vector(scores_on({0: 0.5, UID_STAR: 0.5}), hotkeys, STAR, 1) == ([0], [65535])
    assert allowed_emit_vector(scores_on({0: 0.5, 9: 0.5}), hotkeys, STAR, 1) == ([0, 9], [65535, 65535])


@pytest.mark.parametrize("hotkeys, incentive_hotkey", [
    ([hk for hk in HOTKEYS if hk != STAR], STAR),
    ([STAR] + HOTKEYS[1:UID_STAR] + ["hk-other"] + HOTKEYS[UID_STAR + 1:], STAR),
    (HOTKEYS, ""),
])
def test_absent_unset_or_uid_zero_incentive_hotkey_burns(hotkeys, incentive_hotkey):
    scores = scores_on({0: 0.9, UID_STAR: 0.1}, n=len(hotkeys))
    assert allowed_emit_vector(scores, hotkeys, incentive_hotkey, 1) == ([0], [65535])


def test_zero_or_non_finite_scores_burn():
    assert allowed_emit_vector(np.zeros(256), HOTKEYS, STAR, 1) == ([0], [65535])
    scores = scores_on({UID_STAR: 1.0})
    scores[5] = np.nan
    assert allowed_emit_vector(scores, HOTKEYS, STAR, 1) == ([UID_STAR], [65535])


def test_unknown_min_allowed_weights_is_refused():
    with pytest.raises(WeightVectorRefused, match="min_allowed_weights_unknown"):
        allowed_emit_vector(scores_on({0: 1.0}), HOTKEYS, STAR, None)


@pytest.mark.parametrize("converted, reason", [
    (([0, 5], [65535, 100]), "uid_not_allowed"),
    (([], []), "empty_vector"),
])
def test_emitted_uids_are_checked_after_conversion(monkeypatch, converted, reason):
    monkeypatch.setattr(emission, "convert_weights_and_uids_for_emit", lambda uids, weights: converted)
    with pytest.raises(WeightVectorRefused, match=reason):
        allowed_emit_vector(scores_on({0: 0.9, UID_STAR: 0.1}), HOTKEYS, STAR, 0)
