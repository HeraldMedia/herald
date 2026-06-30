"""Per-article USD score from the exact checks. Feeds the existing emission pipeline."""

from herald.validator.utils.config import (
    HERALD_BASE_PAYOUT_USD,
    HERALD_NO_SEARCH_FLOOR,
    HERALD_TIER_MULTIPLIER,
)


def article_usd(tier: int, in_search: bool, boost: float = 1.0) -> float:
    tier_mult = HERALD_TIER_MULTIPLIER.get(tier, 0.0)
    search_mult = 1.0 if in_search else HERALD_NO_SEARCH_FLOOR
    return HERALD_BASE_PAYOUT_USD * boost * tier_mult * search_mult
