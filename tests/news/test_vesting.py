from herald.validator.news.vesting import VestingLedger


def test_start_and_release_installments():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    amt, clawed = v.release("art1", alive=True, epoch=1)
    assert amt == 100.0 and clawed is False
    assert v.status("art1") == "VESTING"


def test_completes_after_all_installments():
    v = VestingLedger(vest_epochs=2)
    v.start("art1", uid=1, total_usd=400.0)
    assert v.release("art1", alive=True, epoch=1)[0] == 200.0
    assert v.release("art1", alive=True, epoch=2)[0] == 200.0
    assert v.status("art1") == "COMPLETED"
    assert v.release("art1", alive=True, epoch=3) == (0.0, False)


def test_release_is_epoch_gated():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    assert v.release("art1", alive=True, epoch=5)[0] == 100.0
    assert v.release("art1", alive=True, epoch=5) == (0.0, False)  # same epoch, no double release
    assert v.entry("art1").remaining == 3


def test_clawback_on_disappearance():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", alive=True, epoch=1)
    amt, clawed = v.release("art1", alive=False, epoch=2)
    assert amt == 0.0 and clawed is True
    assert v.status("art1") == "CLAWBACK"
    assert v.release("art1", alive=True, epoch=3) == (0.0, False)


def test_reassign_to_earlier_committer():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, hotkey="hk1", commit_epoch=5)
    v.start("art1", uid=2, total_usd=400.0, hotkey="hk2", commit_epoch=3)  # earlier
    assert v.entry("art1").uid == 2 and v.entry("art1").commit_epoch == 3
    v.start("art1", uid=3, total_usd=400.0, hotkey="hk3", commit_epoch=10)  # later, ignored
    assert v.entry("art1").uid == 2


def test_start_is_idempotent_same_committer():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, commit_epoch=5)
    v.release("art1", alive=True, epoch=1)
    v.start("art1", uid=1, total_usd=400.0, commit_epoch=5)  # already vesting; no reset
    assert v.entry("art1").remaining == 3


def test_active_article_ids():
    v = VestingLedger(vest_epochs=2)
    v.start("a", uid=1, total_usd=100.0)
    v.start("b", uid=2, total_usd=100.0)
    v.release("b", alive=False, epoch=1)
    assert set(v.active_article_ids()) == {"a"}


def test_roundtrip():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", alive=True, epoch=1)
    restored = VestingLedger.from_dict(v.to_dict())
    assert restored.entry("art1").remaining == 3 and restored.vest_epochs == 4
