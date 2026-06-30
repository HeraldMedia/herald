from herald.validator.news.state import HeraldState


def test_fresh_state_is_empty():
    s = HeraldState.fresh()
    assert s.vesting.active_article_ids() == []
    assert s.slash.is_slashed("hkA", 0) is False


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "herald_state.json")
    s = HeraldState.fresh()
    s.commit_index.observe({"hkA": ("v1", 100)})
    s.vesting.start("art1", uid=1, total_usd=400.0, url="https://x/a", hotkey="hkA")
    s.slash.slash("hkB", until_epoch=9)
    s.save(path)

    loaded = HeraldState.load(path)
    assert loaded.commit_index.first_seen_block("hkA", "v1") == 100
    assert loaded.vesting.entry("art1").remaining == loaded.vesting.vest_epochs
    assert loaded.slash.is_slashed("hkB", 5) is True


def test_load_missing_file_returns_fresh(tmp_path):
    s = HeraldState.load(str(tmp_path / "absent.json"))
    assert s.vesting.active_article_ids() == []
