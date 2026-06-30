from herald.validator.news.vesting import VestingLedger


def test_start_and_release_installments():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    amt, clawed = v.release("art1", alive=True)
    assert amt == 100.0 and clawed is False
    assert v.status("art1") == "VESTING"


def test_completes_after_all_installments():
    v = VestingLedger(vest_epochs=2)
    v.start("art1", uid=1, total_usd=400.0)
    assert v.release("art1", alive=True)[0] == 200.0
    assert v.release("art1", alive=True)[0] == 200.0
    assert v.status("art1") == "COMPLETED"
    assert v.release("art1", alive=True) == (0.0, False)  # nothing left


def test_clawback_on_disappearance():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", alive=True)
    amt, clawed = v.release("art1", alive=False)
    assert amt == 0.0 and clawed is True
    assert v.status("art1") == "CLAWBACK"
    # no further payment after clawback
    assert v.release("art1", alive=True) == (0.0, False)


def test_start_is_idempotent():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", alive=True)
    v.start("art1", uid=1, total_usd=400.0)  # already vesting; no reset
    assert v.entry("art1").remaining == 3


def test_active_article_ids():
    v = VestingLedger(vest_epochs=2)
    v.start("a", uid=1, total_usd=100.0)
    v.start("b", uid=2, total_usd=100.0)
    v.release("b", alive=False)
    assert set(v.active_article_ids()) == {"a"}


def test_roundtrip():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", alive=True)
    restored = VestingLedger.from_dict(v.to_dict())
    assert restored.entry("art1").remaining == 3 and restored.vest_epochs == 4
