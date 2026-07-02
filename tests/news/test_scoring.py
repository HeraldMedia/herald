from herald.validator.news.scoring import article_usd
from herald.validator.utils.config import HERALD_BASE_PAYOUT_USD


def test_tier_multipliers():
    assert article_usd(tier=1, in_search=True) == HERALD_BASE_PAYOUT_USD * 1.0
    assert article_usd(tier=2, in_search=True) == HERALD_BASE_PAYOUT_USD * 0.5
    assert article_usd(tier=3, in_search=True) == HERALD_BASE_PAYOUT_USD * 0.25


def test_not_in_search_zeroes_by_default():
    assert article_usd(tier=1, in_search=False) == 0.0


def test_unknown_tier_is_zero():
    assert article_usd(tier=9, in_search=True) == 0.0
