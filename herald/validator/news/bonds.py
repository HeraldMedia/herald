"""Bonds are self-locked alpha stake treated as slashable collateral via the weight mechanism."""

from herald.validator.utils.config import HERALD_BOND_ALPHA_PER_USD, SLASH_MULTIPLIER

_ATTO = 10 ** 18


def min_bond_atto(expected_reward_usd: float) -> int:
    """Minimum bond a claim must commit, so cheating's expected value is negative."""
    return int(expected_reward_usd * HERALD_BOND_ALPHA_PER_USD * SLASH_MULTIPLIER * _ATTO)


def bond_ok(alpha_stake: float, active_bond_atto: int) -> bool:
    return int(alpha_stake * _ATTO) >= active_bond_atto
