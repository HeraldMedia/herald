from herald.validator.news.bonds import bond_ok, required_bond_atto

ALPHA = 10 ** 18  # 1 alpha in atto


def test_bond_ok_when_stake_covers_bonds():
    assert bond_ok(alpha_stake=1.0, active_bond_atto=ALPHA) is True
    assert bond_ok(alpha_stake=1.0, active_bond_atto=ALPHA // 2) is True


def test_bond_not_ok_when_underfunded():
    assert bond_ok(alpha_stake=1.0, active_bond_atto=2 * ALPHA) is False


def test_required_bond_scales_with_multiplier():
    assert required_bond_atto(expected_reward_alpha=0.5, multiplier=1.5) == int(0.75 * ALPHA)


def test_zero_bond_always_ok():
    assert bond_ok(alpha_stake=0.0, active_bond_atto=0) is True
