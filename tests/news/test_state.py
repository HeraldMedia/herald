from herald.validator.news.state import HeraldState


def test_fresh_state_is_empty():
    s = HeraldState.fresh()
    assert s.vesting.active_article_ids() == []
    assert s.slash.is_slashed("hkA", 0) is False
    assert s.last_weight_epoch == -1


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "herald_state.json")
    s = HeraldState.fresh()
    s.commit_index.observe({"hkA": ("v1", 100)})
    s.vesting.start("art1", uid=1, total_usd=400.0, url="https://x/a", hotkey="hkA")
    s.slash.slash("hkB", until_epoch=9)
    s.last_scored_epoch = 12
    s.last_weight_epoch = 12
    s.save(path)

    loaded = HeraldState.load(path)
    assert loaded.commit_index.first_seen_block("hkA", "v1") == 100
    assert loaded.vesting.entry("art1").remaining == loaded.vesting.vest_epochs
    assert loaded.slash.is_slashed("hkB", 5) is True
    assert loaded.last_scored_epoch == 12
    assert loaded.last_weight_epoch == 12


def test_consensus_divisors_come_from_config_not_persisted_state():
    # epoch_len / vest_epochs decide commit-ordering and installment size; a drifted value
    # persisted by an older validator must not override the current config on load, or two
    # validators disagree on the winner / payout.
    from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS
    base = HeraldState.fresh()
    base.commit_index.observe({"hkX": ("v", 5)})
    d = base.to_dict()
    d["commit_index"]["epoch_len"] = EPOCH_LEN + 123  # simulate a stale/old state file
    d["vesting"]["vest_epochs"] = VEST_EPOCHS + 7
    s = HeraldState.from_dict(d)
    assert s.commit_index.epoch_len == EPOCH_LEN
    assert s.vesting.vest_epochs == VEST_EPOCHS
    assert s.commit_index.first_seen_block("hkX", "v") == 5  # persisted data still restored


def test_legacy_state_defaults_to_no_submitted_weight_epoch():
    data = HeraldState.fresh().to_dict()
    data.pop("last_weight_epoch")

    assert HeraldState.from_dict(data).last_weight_epoch == -1


def test_load_missing_file_returns_fresh(tmp_path):
    s = HeraldState.load(str(tmp_path / "absent.json"))
    assert s.vesting.active_article_ids() == []


def test_load_corrupt_file_returns_fresh(tmp_path):
    path = tmp_path / "herald_state.json"
    path.write_text("{ this is not valid json")
    s = HeraldState.load(str(path))  # must not raise / wedge
    assert s.vesting.active_article_ids() == []


def test_save_is_atomic_no_partial_temp(tmp_path):
    path = str(tmp_path / "herald_state.json")
    HeraldState.fresh().save(path)
    import os
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [] and os.path.exists(path)
