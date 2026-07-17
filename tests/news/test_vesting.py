from herald.validator.news.vesting import VestingLedger


def test_start_and_release_installments():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, start_epoch=1)
    assert v.release("art1", epoch=1) == 100.0
    assert v.status("art1") == "VESTING"


def test_completes_after_all_installments():
    v = VestingLedger(vest_epochs=2)
    v.start("art1", uid=1, total_usd=400.0, start_epoch=1)
    assert v.release("art1", epoch=1) == 200.0
    assert v.release("art1", epoch=2) == 200.0
    assert v.status("art1") == "COMPLETED"
    assert v.release("art1", epoch=3) == 0.0


def test_release_is_epoch_gated():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, start_epoch=5)
    assert v.release("art1", epoch=5) == 100.0
    assert v.release("art1", epoch=5) == 0.0  # same epoch, no double release
    assert v.entry("art1").remaining == 3


def test_release_catches_up_missed_epochs():
    # Epochs missed while the validator was down (or the article held) release in a lump, so the
    # vest stays tied to chain time rather than scoring-pass count.
    v = VestingLedger(vest_epochs=30)
    v.start("art1", uid=1, total_usd=300.0, start_epoch=10)
    assert v.release("art1", epoch=10) == 10.0    # first epoch: one installment
    assert v.release("art1", epoch=14) == 40.0    # 4 missed epochs catch up
    assert v.entry("art1").remaining == 25
    assert v.release("art1", epoch=100) == 250.0  # capped at remaining
    assert v.status("art1") == "COMPLETED"


def test_clawback_stops_future_release():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0)
    v.release("art1", epoch=1)
    assert v.clawback("art1") is True
    assert v.status("art1") == "CLAWBACK"
    assert v.release("art1", epoch=2) == 0.0
    assert v.clawback("art1") is False  # already clawed back


def test_hold_does_not_advance_or_clawback():
    # "hold" = caller simply doesn't call release/clawback; the entry must be untouched
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, start_epoch=1)
    v.release("art1", epoch=1)
    remaining = v.entry("art1").remaining
    # epoch 2 is a transient outage: caller holds (no calls)
    assert v.status("art1") == "VESTING" and v.entry("art1").remaining == remaining
    assert v.release("art1", epoch=3) == 200.0  # recovers: the held epoch catches up too


def test_reassign_to_earlier_committer():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, hotkey="hk1", commit_epoch=5)
    v.start("art1", uid=2, total_usd=400.0, hotkey="hk2", commit_epoch=3)  # earlier
    assert v.entry("art1").uid == 2 and v.entry("art1").commit_epoch == 3
    v.start("art1", uid=3, total_usd=400.0, hotkey="hk3", commit_epoch=10)  # later, ignored
    assert v.entry("art1").uid == 2


def test_reassign_after_vest_epochs_change_cannot_overpay():
    # If the ledger divisor changes mid-flight (operator edits HERALD_VEST_EPOCHS + restart;
    # config is now the source of truth), a later earliest-commit reassignment must not
    # recompute the installment against the new divisor while keeping the old remaining —
    # lifetime payout must still never exceed total_usd.
    v = VestingLedger(vest_epochs=10)
    v.start("art", uid=1, total_usd=300.0, hotkey="hk1", commit_epoch=5)
    paid = sum(v.release("art", epoch=e) for e in range(1, 6))  # 5 x 30 = 150, remaining 5
    v.vest_epochs = 2  # operator lowered the schedule and restarted
    v.start("art", uid=2, total_usd=300.0, hotkey="hk2", commit_epoch=3)  # earlier -> reassign
    e = 5
    while v.status("art") == "VESTING":
        e += 1
        paid += v.release("art", epoch=e)
    assert paid <= 300.0 + 1e-9
    assert v.entry("art").uid == 2  # the payee still got reassigned


def test_start_is_idempotent_same_committer():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, commit_epoch=5, start_epoch=1)
    v.release("art1", epoch=1)
    v.start("art1", uid=1, total_usd=400.0, commit_epoch=5, start_epoch=1)  # already vesting; no reset
    assert v.entry("art1").remaining == 3


def test_active_article_ids():
    v = VestingLedger(vest_epochs=2)
    v.start("a", uid=1, total_usd=100.0)
    v.start("b", uid=2, total_usd=100.0)
    v.clawback("b")
    assert set(v.active_article_ids()) == {"a"}


def test_roundtrip():
    v = VestingLedger(vest_epochs=4)
    v.start("art1", uid=1, total_usd=400.0, start_epoch=1,
            outlet_id="guardian", tier=1, attribution=2, reveal={"nonce": "n"})
    v.release("art1", epoch=1)
    restored = VestingLedger.from_dict(v.to_dict())
    assert restored.entry("art1").remaining == 3 and restored.vest_epochs == 4
    assert restored.entry("art1").outlet_id == "guardian"
    assert restored.entry("art1").reveal == {"nonce": "n"}


def test_expire_terminates_held_entry():
    v = VestingLedger(vest_epochs=2)
    v.start("a", uid=1, total_usd=100.0, start_epoch=0)
    assert v.expire("a") is True
    assert v.status("a") == "EXPIRED"
    assert v.active_article_ids() == []
    assert v.release("a", epoch=5) == 0.0  # terminal: no further pay
