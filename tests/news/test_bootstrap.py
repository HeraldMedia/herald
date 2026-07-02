from herald.validator.news.bootstrap import bootstrap_state

HK2UID = {"hkA": 1, "hkB": 2}


def row(**over):
    base = dict(article_id="art1", hotkey="hkA", brief_id="b1", usd=300.0,
                status="VESTING", start_epoch=100, commit_epoch=95,
                url="https://x/a")
    base.update(over)
    return base


def test_mid_vest_import_is_forward_only():
    # Joined at epoch 110; article started at 100 -> 11 installments already released elsewhere.
    state = bootstrap_state([row()], HK2UID, current_epoch=110, vest_epochs=30)
    entry = state.vesting.entry("art1")
    assert entry.uid == 1 and entry.remaining == 30 - 11 and entry.commit_epoch == 95
    # No retro pay: nothing releases for the join epoch; the next epoch releases exactly one.
    assert state.vesting.release("art1", 110) == 0.0
    assert state.vesting.release("art1", 111) == 300.0 / 30
    # Pool draw-down approximates what incumbents already paid out.
    assert state.pool_spent["b1"] == 11 * (300.0 / 30)
    assert state.last_scored_epoch == 110


def test_expired_unknown_and_malformed_rows_skipped():
    rows = [
        row(article_id="old", start_epoch=10),               # fully vested by epoch 110
        row(article_id="gone", hotkey="hkGONE"),             # hotkey left the metagraph
        row(article_id="claw", status="CLAWBACK"),           # terminal
        {"article_id": "legacy", "hotkey": "hkA", "usd": 100.0, "status": "VESTING"},  # no start_epoch
        row(article_id="ok"),
    ]
    state = bootstrap_state(rows, HK2UID, current_epoch=110, vest_epochs=30)
    assert state.vesting.active_article_ids() == ["ok"]
    # The expired article still contributes to the pool draw-down it caused.
    assert state.pool_spent["b1"] == 30 * (300.0 / 30) + 11 * (300.0 / 30)


def test_fresh_start_epoch_keeps_full_schedule():
    state = bootstrap_state([row(start_epoch=110)], HK2UID, current_epoch=110, vest_epochs=30)
    assert state.vesting.entry("art1").remaining == 29  # the start-epoch installment was released
