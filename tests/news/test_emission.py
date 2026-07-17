import pytest

from herald.validator.news.emission import apply_reward_pools, compute_weights
from herald.validator.news.vesting import VestingLedger


def test_single_rewarded_miner_receives_all_weight():
    weights = compute_weights({1: 500.0 / 30}, uids=[0, 1])

    assert weights[0] == 0.0
    assert weights[1] == pytest.approx(1.0)
    assert weights.sum() == pytest.approx(1.0)


def test_daily_installments_normalize_across_rewarded_miners():
    weights = compute_weights(
        {1: 500.0 / 30, 2: 700.0 / 30, 3: 1000.0 / 30},
        uids=[0, 1, 2, 3],
    )

    assert weights[0] == 0.0
    assert weights[1] == pytest.approx(5 / 22)
    assert weights[2] == pytest.approx(7 / 22)
    assert weights[3] == pytest.approx(10 / 22)
    assert weights.sum() == pytest.approx(1.0)


def test_overlapping_30_day_installments_aggregate_before_normalization():
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
    weights = compute_weights(daily_usd, uids=[0, 1, 2, 3])

    assert daily_usd[3] == pytest.approx((500.0 / 30) * 2)
    assert weights[1] == pytest.approx(5 / 22)
    assert weights[2] == pytest.approx(7 / 22)
    assert weights[3] == pytest.approx(10 / 22)


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


def test_no_rewarded_miners_returns_zero_vector():
    weights = compute_weights({1: 0.0, 2: -10.0}, uids=[0, 1, 2])

    assert weights.tolist() == [0.0, 0.0, 0.0]


def test_unregistered_uid_is_excluded_from_normalization():
    weights = compute_weights({1: 10.0, 99: 90.0}, uids=[0, 1])

    assert weights.tolist() == [0.0, 1.0]


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
    assert compute_weights(forward, [0, 2, 3]).tolist() == compute_weights(
        reverse, [0, 2, 3]
    ).tolist()


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
    clips those two proportionally, all three paid miners normalize to 100%, and the exhausted
    client brief contributes nothing on the next epoch while the standing placement continues.
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
    weights_1052 = compute_weights(paid_1052, [0, 1, 2, 3])

    assert paid_1052 == {
        1: pytest.approx(500.0 / 30),
        2: pytest.approx(12.5),
        3: pytest.approx(7.5),
    }
    assert weights_1052.tolist() == pytest.approx([0.0, 5 / 11, 15 / 44, 9 / 44])
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
    assert compute_weights(paid_1053, [0, 1, 2, 3]).tolist() == [0.0, 1.0, 0.0, 0.0]
    assert spent["client"] == pytest.approx(20.0)
