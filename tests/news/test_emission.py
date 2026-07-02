import pytest

from herald.validator.news.emission import apply_reward_pools, compute_weights


def test_remainder_burns_to_burn_uid():
    # $300 of work against $1000 of daily emissions -> 0.3 paid, 0.7 burned to UID 0
    w = compute_weights({1: 300.0}, uids=[0, 1], total_daily_usd=1000.0, burn_uid=0)
    assert w[1] == pytest.approx(0.3)
    assert w[0] == pytest.approx(0.7)
    assert w.sum() == pytest.approx(1.0)


def test_no_work_burns_everything():
    w = compute_weights({1: 0.0, 2: 0.0}, uids=[0, 1, 2], total_daily_usd=1000.0, burn_uid=0)
    assert w[0] == pytest.approx(1.0) and w[1] == 0.0 and w[2] == 0.0


def test_work_above_daily_splits_fully_no_burn():
    w = compute_weights({1: 600.0, 2: 600.0}, uids=[0, 1, 2], total_daily_usd=1000.0, burn_uid=0)
    assert w[1] == pytest.approx(0.5) and w[2] == pytest.approx(0.5) and w[0] == 0.0


def test_multiple_miners_with_burn():
    w = compute_weights({1: 300.0, 2: 200.0}, uids=[0, 1, 2], total_daily_usd=1000.0, burn_uid=0)
    assert w[1] == pytest.approx(0.3) and w[2] == pytest.approx(0.2) and w[0] == pytest.approx(0.5)


def test_zero_daily_usd_no_burn_proportional():
    w = compute_weights({1: 2.0, 2: 1.0}, uids=[0, 1, 2], total_daily_usd=0.0, burn_uid=0)
    assert w[1] == pytest.approx(2 / 3) and w[2] == pytest.approx(1 / 3) and w[0] == 0.0


def test_all_zero_and_zero_daily_returns_zeros():
    w = compute_weights({1: 0.0}, uids=[0, 1], total_daily_usd=0.0, burn_uid=0)
    assert w.sum() == 0.0


def test_client_brief_under_pool_unchanged():
    spent = {}
    usd = apply_reward_pools({(1, "c1"): 300.0}, [{"id": "c1", "kind": "client", "reward_pool": 500.0}],
                             total_daily_usd=1000.0, pool_spent=spent)
    assert usd == {1: 300.0} and spent["c1"] == pytest.approx(300.0)


def test_client_brief_over_pool_scaled_down():
    # pool 200; brief total 400 -> scale 0.5, pool fully spent
    spent = {}
    usd = apply_reward_pools({(1, "c1"): 300.0, (2, "c1"): 100.0},
                             [{"id": "c1", "kind": "client", "reward_pool": 200.0}],
                             total_daily_usd=1000.0, pool_spent=spent)
    assert usd[1] == pytest.approx(150.0) and usd[2] == pytest.approx(50.0)
    assert spent["c1"] == pytest.approx(200.0)


def test_client_pool_drawn_down_across_epochs():
    briefs = [{"id": "c1", "kind": "client", "reward_pool": 100.0}]
    spent = {}
    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, 1000.0, spent)[1] == pytest.approx(80.0)
    # only 20 left -> the next 80 earned is scaled down to 20
    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, 1000.0, spent)[1] == pytest.approx(20.0)
    # pool exhausted -> pays nothing more
    assert apply_reward_pools({(1, "c1"): 80.0}, briefs, 1000.0, spent).get(1, 0.0) == 0.0
    assert spent["c1"] == pytest.approx(100.0)


def test_unfunded_client_brief_pays_zero():
    # no reward_pool -> no budget to pay from (only funded briefs pay)
    usd = apply_reward_pools({(1, "c1"): 500.0}, [{"id": "c1", "kind": "client"}], 1000.0, {})
    assert usd.get(1, 0.0) == 0.0


def test_standing_brief_takes_remainder():
    # client draws 300 from its pool; the standing brief earns 900 but only 700 of budget remains
    spent = {}
    usd = apply_reward_pools(
        {(1, "c1"): 300.0, (2, "s1"): 900.0},
        [{"id": "c1", "kind": "client", "reward_pool": 300.0}, {"id": "s1", "kind": "standing"}],
        total_daily_usd=1000.0, pool_spent=spent,
    )
    assert usd[1] == pytest.approx(300.0)      # client paid from its pool
    assert usd[2] == pytest.approx(700.0)      # standing capped at the remainder (1000 - 300)
    assert spent["c1"] == pytest.approx(300.0)


def test_reward_pools_order_independent():
    # two client briefs, each capped by its own pool -> the split is identical regardless of order
    briefs = [{"id": "a", "kind": "client", "reward_pool": 100.0},
              {"id": "b", "kind": "client", "reward_pool": 100.0}]
    forward = apply_reward_pools({(1, "a"): 300.0, (2, "b"): 300.0}, briefs, 1000.0, {})
    reverse = apply_reward_pools({(2, "b"): 300.0, (1, "a"): 300.0}, briefs, 1000.0, {})
    assert forward == reverse
    assert forward == {1: pytest.approx(100.0), 2: pytest.approx(100.0)}


def test_zero_daily_skips_pools():
    usd = apply_reward_pools({(1, "c1"): 400.0}, [{"id": "c1", "kind": "client", "reward_pool": 10.0}],
                             total_daily_usd=0.0, pool_spent={})
    assert usd[1] == pytest.approx(400.0)


def test_aggregate_client_draw_capped_at_daily_budget():
    # Two well-funded client briefs together earn 1500 against a 1000 budget: scaled to 2/3 each,
    # standing budget (and burn) stay at 0 instead of going negative, and pools draw the scaled pay.
    briefs = [{"id": "a", "kind": "client", "reward_pool": 5000.0},
              {"id": "b", "kind": "client", "reward_pool": 5000.0},
              {"id": "s1", "kind": "standing"}]
    spent = {}
    usd = apply_reward_pools({(1, "a"): 900.0, (2, "b"): 600.0, (3, "s1"): 100.0},
                             briefs, total_daily_usd=1000.0, pool_spent=spent)
    assert usd[1] == pytest.approx(600.0)   # 900 * 2/3
    assert usd[2] == pytest.approx(400.0)   # 600 * 2/3
    assert usd.get(3, 0.0) == 0.0           # no budget left for the standing brief
    assert usd[1] + usd[2] == pytest.approx(1000.0)
    assert spent["a"] == pytest.approx(600.0) and spent["b"] == pytest.approx(400.0)
