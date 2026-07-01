"""Per-article USD score from the exact checks. Feeds the existing emission pipeline."""

from herald.validator.utils.config import (
    HERALD_BASE_PAYOUT_USD,
    HERALD_FUND_BOOST_MAX,
    HERALD_NO_SEARCH_FLOOR,
    HERALD_TIER_MULTIPLIER,
)
from .funding import clamp_boost


def article_usd(tier: int, in_search: bool, boost: float = 1.0) -> float:
    tier_mult = HERALD_TIER_MULTIPLIER.get(tier, 0.0)
    search_mult = 1.0 if in_search else HERALD_NO_SEARCH_FLOOR
    boost_eff = clamp_boost(boost, HERALD_FUND_BOOST_MAX)  # consensus rail: boost in [1, max]
    return HERALD_BASE_PAYOUT_USD * boost_eff * tier_mult * search_mult
