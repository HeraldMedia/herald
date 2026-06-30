"""Bonds are self-locked alpha stake treated as slashable collateral via the weight mechanism."""

_ATTO = 10 ** 18


def required_bond_atto(expected_reward_alpha: float, multiplier: float) -> int:
    return int(expected_reward_alpha * multiplier * _ATTO)


def bond_ok(alpha_stake: float, active_bond_atto: int) -> bool:
    return int(alpha_stake * _ATTO) >= active_bond_atto
