from herald.validator.news.disputes import REJECTED, UPHELD, DisputeLedger


def test_open_one_per_article_idempotent():
    led = DisputeLedger()
    assert led.open("art1", "hkA", 5) is True
    assert led.is_disputed("art1")
    # earliest filer wins; a re-read or later filer is ignored (caller registers in block order)
    assert led.open("art1", "hkB", 6) is False
    assert led.active("art1").disputer_hotkey == "hkA"


def test_resolve_upheld_and_rejected():
    led = DisputeLedger()
    led.open("art1", "hkA", 5)
    assert led.resolve("art1", upheld=True).status == UPHELD
    assert not led.is_disputed("art1")               # resolved -> no longer open
    assert led.resolve("art1", upheld=True) is None  # already resolved
    led.open("art2", "hkB", 5)
    assert led.resolve("art2", upheld=False).status == REJECTED


def test_persist_round_trip():
    led = DisputeLedger()
    led.open("art1", "hkA", 5)
    led.resolve("art1", upheld=True)
    led.open("art2", "hkB", 6)
    again = DisputeLedger.from_dict(led.to_dict())
    assert again.active("art2").disputer_hotkey == "hkB"
    assert not again.is_disputed("art1")             # upheld persists as resolved
    assert again.to_dict()["art1"]["status"] == UPHELD
