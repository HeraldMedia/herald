from herald.validator.news.slashing import SlashLedger


def test_slash_blocks_during_cooldown():
    s = SlashLedger()
    s.slash("hkA", until_epoch=10)
    assert s.is_slashed("hkA", 5) is True
    assert s.is_slashed("hkA", 10) is False  # cooldown ended
    assert s.is_slashed("hkB", 5) is False


def test_slash_extends_to_max():
    s = SlashLedger()
    s.slash("hkA", until_epoch=10)
    s.slash("hkA", until_epoch=5)   # shorter; should not shorten
    assert s.is_slashed("hkA", 8) is True


def test_roundtrip():
    s = SlashLedger()
    s.slash("hkA", until_epoch=10)
    restored = SlashLedger.from_dict(s.to_dict())
    assert restored.is_slashed("hkA", 9) is True
