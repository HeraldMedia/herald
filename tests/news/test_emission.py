import pytest

from herald.validator.news.emission import compute_weights


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
