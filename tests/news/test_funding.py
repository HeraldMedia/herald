import math

from herald.validator.news.funding import boost_for_alpha, clamp_boost


def test_clamp_boost_bounds():
    assert clamp_boost(1.0, 3.0) == 1.0
    assert clamp_boost(2.0, 3.0) == 2.0
    assert clamp_boost(3.0, 3.0) == 3.0
    assert clamp_boost(9.9, 3.0) == 3.0      # above max -> max (the consensus rail)
    assert clamp_boost(0.2, 3.0) == 1.0      # below 1 -> 1 (no negative boosting)


def test_clamp_boost_handles_garbage():
    assert clamp_boost(float("nan"), 3.0) == 1.0
    assert clamp_boost("oops", 3.0) == 1.0
    assert clamp_boost(None, 3.0) == 1.0


def test_boost_curve_endpoints_and_shape():
    M, A = 3.0, 10000.0
    assert boost_for_alpha(0, A, M) == 1.0
    assert boost_for_alpha(-5, A, M) == 1.0
    assert math.isclose(boost_for_alpha(A, A, M), 3.0)        # full funding -> max
    assert math.isclose(boost_for_alpha(A / 4, A, M), 2.0)    # quarter α -> 2x (sqrt front-loads)
    assert boost_for_alpha(A * 4, A, M) == 3.0                # over-funding capped at max
    # monotonic non-decreasing
    vals = [boost_for_alpha(a, A, M) for a in (0, 100, 1000, 5000, 10000, 20000)]
    assert vals == sorted(vals)
