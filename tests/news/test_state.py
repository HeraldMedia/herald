import json
import os
import re
from pathlib import Path

import pytest

from herald.validator.news import state as state_mod
from herald.validator.news.state import (
    ALLOW_FRESH_ON_CORRUPT_ENV,
    ALLOW_NEWER_SCHEMA_ENV,
    BACKUP_KEEP_ENV,
    DEFAULT_BACKUP_KEEP,
    SCHEMA_VERSION,
    HeraldState,
    HeraldStateLoadError,
    backup_paths,
)
from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS

LEGACY_FILE = os.path.join(
    os.path.dirname(__file__), "fixtures", "state_rollback", "legacy_schema1_state.json"
)


@pytest.fixture(autouse=True)
def _state_env(monkeypatch):
    monkeypatch.delenv(ALLOW_FRESH_ON_CORRUPT_ENV, raising=False)
    monkeypatch.delenv(ALLOW_NEWER_SCHEMA_ENV, raising=False)
    monkeypatch.delenv(BACKUP_KEEP_ENV, raising=False)


def _capture(monkeypatch, level):
    messages = []
    monkeypatch.setattr(state_mod.bt.logging, level,
                        lambda msg="", *args, **kwargs: messages.append(str(msg)))
    return messages


def _saved(path, epoch):
    state = HeraldState.fresh()
    state.last_scored_epoch = epoch
    state.save(str(path))
    return state


def _epoch_of(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["last_scored_epoch"]


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


# --- schema_version -------------------------------------------------------------------------------

def test_saved_file_carries_schema_version(tmp_path):
    path = tmp_path / "herald_state.json"
    HeraldState.fresh().save(str(path))
    assert SCHEMA_VERSION == 2
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_legacy_file_without_schema_version_loads_unchanged():
    with open(LEGACY_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    assert "schema_version" not in raw  # legacy schema 1, as the previous release writes

    loaded = HeraldState.from_dict(raw)

    assert loaded.last_scored_epoch == 12 and loaded.last_weight_epoch == -1
    assert loaded.commit_index.first_seen_block("hkLegacyA", "HRLD1|00aa11bb22cc33dd") == 1000
    expected = json.loads(json.dumps(raw))
    expected["commit_index"]["epoch_len"] = EPOCH_LEN
    expected["vesting"]["vest_epochs"] = VEST_EPOCHS
    assert loaded.to_dict() == {**expected, "schema_version": SCHEMA_VERSION}


def test_newer_schema_version_loads_what_it_understands_with_warning(monkeypatch):
    warnings = _capture(monkeypatch, "warning")
    data = HeraldState.fresh().to_dict()
    data["schema_version"] = SCHEMA_VERSION + 1
    data["future_section"] = {"epoch": 1}

    loaded = HeraldState.from_dict(data).to_dict()

    assert loaded["schema_version"] == SCHEMA_VERSION and "future_section" not in loaded
    assert any(f"schema_version {SCHEMA_VERSION + 1}" in w for w in warnings)
    assert any("'future_section'" in w for w in warnings)


def _newer_file(tmp_path, **extra):
    data = HeraldState.fresh().to_dict()
    data.update({"schema_version": SCHEMA_VERSION + 1, "last_scored_epoch": 41,
                 "future_section": {"epoch": 1}, **extra})
    path = tmp_path / "herald_state.json"
    path.write_text(json.dumps(data))
    return path


def _copies(tmp_path, kind):
    return sorted(p for p in tmp_path.iterdir() if p.name.startswith(f"herald_state.json.{kind}."))


def test_newer_additive_file_loads_after_the_original_is_copied_aside(tmp_path, monkeypatch):
    monkeypatch.setenv(BACKUP_KEEP_ENV, "1")
    path = _newer_file(tmp_path)
    original = path.read_bytes()
    warnings = _capture(monkeypatch, "warning")

    assert HeraldState.load(str(path)).last_scored_epoch == 41
    copies = _copies(tmp_path, f"schema{SCHEMA_VERSION + 1}")
    assert len(copies) == 1 and copies[0].read_bytes() == original
    assert any(str(copies[0]) in w for w in warnings)

    for epoch in range(42, 46):
        _saved(path, epoch)
    assert copies[0].read_bytes() == original  # rotation never prunes it
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_file_needing_a_newer_reader_refuses_to_start(tmp_path):
    path = _newer_file(tmp_path, min_reader_schema=SCHEMA_VERSION + 1)
    original = path.read_bytes()

    with pytest.raises(HeraldStateLoadError) as info:
        HeraldState.load(str(path))

    message = str(info.value)
    assert message.startswith("Refusing to start") and ALLOW_NEWER_SCHEMA_ENV in message
    assert ALLOW_FRESH_ON_CORRUPT_ENV not in message  # an empty ledger is not the remedy
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["herald_state.json"]


def test_newer_reader_override_loads_after_copying_the_file_aside(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_NEWER_SCHEMA_ENV, "true")
    path = _newer_file(tmp_path, min_reader_schema=SCHEMA_VERSION + 1)
    original = path.read_bytes()

    assert HeraldState.load(str(path)).last_scored_epoch == 41
    copies = _copies(tmp_path, f"schema{SCHEMA_VERSION + 1}")
    assert len(copies) == 1 and copies[0].read_bytes() == original


def test_fresh_on_corrupt_does_not_override_a_newer_reader_requirement(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_FRESH_ON_CORRUPT_ENV, "true")
    path = _newer_file(tmp_path, min_reader_schema=SCHEMA_VERSION + 1)

    with pytest.raises(HeraldStateLoadError, match=ALLOW_NEWER_SCHEMA_ENV):
        HeraldState.load(str(path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["herald_state.json"]


def test_newer_file_is_not_loaded_without_its_copy(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state_mod.shutil, "copy2", fail)
    path = _newer_file(tmp_path)

    with pytest.raises(HeraldStateLoadError, match="disk full"):
        HeraldState.load(str(path))


def test_restarts_before_a_save_keep_a_single_copy_of_a_newer_file(tmp_path):
    path = _newer_file(tmp_path)

    for _ in range(3):
        assert HeraldState.load(str(path)).last_scored_epoch == 41

    assert len(_copies(tmp_path, f"schema{SCHEMA_VERSION + 1}")) == 1


# --- unknown record keys are filtered, never fatal ------------------------------------------------

def test_unknown_vest_entry_keys_are_dropped_with_a_warning(monkeypatch):
    warnings = _capture(monkeypatch, "warning")
    s = HeraldState.fresh()
    s.vesting.start("art1", uid=3, total_usd=300.0, url="https://x/a", hotkey="hkA", start_epoch=7)
    data = s.to_dict()
    data["vesting"]["entries"]["art1"]["future_int_field"] = 10_000_000
    data["vesting"]["entries"]["art1"]["future_str_field"] = "x"

    loaded = HeraldState.from_dict(data)

    entry = loaded.vesting.entry("art1")
    assert (entry.uid, entry.total_usd, entry.hotkey, entry.start_epoch) == (3, 300.0, "hkA", 7)
    assert loaded.vesting.active_article_ids() == ["art1"]
    assert "future_int_field" not in loaded.to_dict()["vesting"]["entries"]["art1"]
    assert any("'future_int_field'" in w and "vesting entry" in w for w in warnings)
    assert any("'future_str_field'" in w for w in warnings)


def test_unknown_dispute_keys_are_dropped_with_a_warning(monkeypatch):
    warnings = _capture(monkeypatch, "warning")
    data = HeraldState.fresh().to_dict()
    data["disputes"] = {"art1": {"article_id": "art1", "disputer_hotkey": "hkD",
                                 "filed_epoch": 4, "status": "open", "future_int_field": 5}}

    loaded = HeraldState.from_dict(data)

    assert loaded.disputes.is_disputed("art1")
    assert any("'future_int_field'" in w and "dispute" in w for w in warnings)


# --- refuse to start on an unreadable file --------------------------------------------------------

@pytest.mark.parametrize("content", [
    "{ this is not valid json",
    "",
    "[]",
    '{"schema_version": "two", "slash": {"until": {}}}',
    '{"commit_index": {}, "vesting": {"entries": {}}}',
    '{"slash": {"until": {}}, "vesting": {"entries": {"art1": {"total_usd": 1.0}}}}',
    '{"slash": {"until": {}}, "vesting": {"entries": {"art1": "not-an-object"}}}',
    '{"slash": {"until": {}}, "last_scored_epoch": null}',
    '{"slash": {"until": {}}, "last_weight_epoch": "12"}',
    '{"slash": {"until": {}}, "last_scored_epoch": true}',
    '{"slash": {"until": {}}, "pool_spent": []}',
    '{"slash": {"until": {}}, "pool_spent": {"brief-1": "5.0"}}',
    '{"slash": {"until": []}}',
    '{"slash": {"until": {"hkA": "9"}}}',
    '{"slash": {"until": {}}, "commit_index": {"first_seen": {"k": null}}}',
    '{"slash": {"until": {}}, "disputes": null}',
    '{"slash": {"until": {}}, "vesting": {"entries": {"art1": {"uid": 1, "total_usd": "400", '
    '"installment_usd": 13.3, "remaining": 30, "status": "VESTING"}}}}',
    '{"slash": {"until": {}}, "vesting": {"entries": {"art1": {"uid": 1, "total_usd": 400.0, '
    '"installment_usd": 13.3, "remaining": 30, "status": "VESTING", "reveal": "nonce"}}}}',
    '{"slash": {"until": {}}, "disputes": {"art1": {"article_id": "art1", '
    '"disputer_hotkey": "hkD", "filed_epoch": "4"}}}',
    '{"slash": {"until": {}}, "schema_version": 3, "min_reader_schema": "3"}',
    '{"slash": {"until": {}}, "schema_version": 2, "min_reader_schema": 3}',
], ids=["bad-json", "empty", "not-object", "bad-schema-version", "no-slash-ledger",
        "entry-missing-required-fields", "entry-not-object", "null-epoch", "string-epoch",
        "bool-epoch", "pool-spent-not-object", "pool-spent-not-number", "slash-until-not-object",
        "slash-until-not-int", "first-seen-not-int", "disputes-null", "entry-number-as-string",
        "entry-reveal-not-object", "dispute-epoch-not-int", "bad-min-reader-schema",
        "min-reader-above-schema"])
def test_unreadable_existing_file_refuses_to_start(tmp_path, content):
    path = tmp_path / "herald_state.json"
    path.write_text(content)

    with pytest.raises(HeraldStateLoadError) as info:
        HeraldState.load(str(path))

    message = str(info.value)
    assert message.startswith("Refusing to start") and str(path) in message
    assert type(info.value.error).__name__ in message and ALLOW_FRESH_ON_CORRUPT_ENV in message
    assert path.read_text() == content  # untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == ["herald_state.json"]


@pytest.mark.parametrize("value", ["", "false", "0", "no", "maybe"])
def test_escape_hatch_needs_an_explicit_true(tmp_path, monkeypatch, value):
    monkeypatch.setenv(ALLOW_FRESH_ON_CORRUPT_ENV, value)
    path = tmp_path / "herald_state.json"
    path.write_text("{ corrupt")
    with pytest.raises(HeraldStateLoadError):
        HeraldState.load(str(path))


def test_escape_hatch_preserves_the_corrupt_file_then_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_FRESH_ON_CORRUPT_ENV, "true")
    errors = _capture(monkeypatch, "error")
    path = tmp_path / "herald_state.json"
    path.write_text("{ corrupt")

    s = HeraldState.load(str(path))

    assert s.vesting.active_article_ids() == [] and s.last_scored_epoch == -1
    preserved = [p for p in tmp_path.iterdir() if p.name.startswith("herald_state.json.corrupt.")]
    assert len(preserved) == 1 and preserved[0].read_text() == "{ corrupt"
    assert path.read_text() == "{ corrupt"  # never deleted
    assert len(errors) == 1
    assert str(preserved[0]) in errors[0] and ALLOW_FRESH_ON_CORRUPT_ENV in errors[0]


def test_escape_hatch_still_refuses_if_the_file_cannot_be_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_FRESH_ON_CORRUPT_ENV, "true")

    def fail(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(state_mod.shutil, "copy2", fail)
    path = tmp_path / "herald_state.json"
    path.write_text("{ corrupt")

    with pytest.raises(HeraldStateLoadError, match="read-only file system"):
        HeraldState.load(str(path))
    assert path.read_text() == "{ corrupt"


def test_load_missing_file_returns_fresh(tmp_path):
    s = HeraldState.load(str(tmp_path / "absent.json"))
    assert s.vesting.active_article_ids() == []


def _lost_file_with_backups(tmp_path):
    path = tmp_path / "herald_state.json"
    for epoch in (3, 4, 5):
        _saved(path, epoch)
    path.unlink()
    return path, backup_paths(str(path))


def test_missing_file_beside_backups_refuses_to_start(tmp_path):
    path, backups = _lost_file_with_backups(tmp_path)

    with pytest.raises(HeraldStateLoadError) as info:
        HeraldState.load(str(path))

    message = str(info.value)
    assert message.startswith("Refusing to start") and "is missing" in message
    assert backups[-1] in message and ALLOW_FRESH_ON_CORRUPT_ENV in message
    assert backup_paths(str(path)) == backups and not path.exists()


def test_escape_hatch_beside_backups_starts_fresh_and_keeps_them(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_FRESH_ON_CORRUPT_ENV, "true")
    monkeypatch.setenv(BACKUP_KEEP_ENV, "1")
    path, backups = _lost_file_with_backups(tmp_path)
    contents = [Path(b).read_bytes() for b in backups]
    errors = _capture(monkeypatch, "error")

    assert HeraldState.load(str(path)).last_scored_epoch == -1
    assert len(errors) == 1
    for epoch in range(6, 10):  # from here on rotation keeps a single backup of its own
        _saved(path, epoch)

    kept = [b + ".kept" for b in backups]
    assert [Path(k).read_bytes() for k in kept] == contents
    assert kept[-1] in errors[0]
    assert [_epoch_of(b) for b in backup_paths(str(path))] == [8]


@pytest.mark.parametrize("name", [ALLOW_FRESH_ON_CORRUPT_ENV, ALLOW_NEWER_SCHEMA_ENV])
def test_an_armed_override_warns_on_every_load(tmp_path, monkeypatch, name):
    path = tmp_path / "herald_state.json"
    _saved(path, 7)
    monkeypatch.setenv(name, "true")
    warnings = _capture(monkeypatch, "warning")

    assert HeraldState.load(str(path)).last_scored_epoch == 7
    assert HeraldState.load(str(path)).last_scored_epoch == 7
    assert len([w for w in warnings if name in w]) == 2


# --- atomic save with dated rotating backups ------------------------------------------------------

def test_save_is_atomic_no_partial_temp(tmp_path):
    path = str(tmp_path / "herald_state.json")
    HeraldState.fresh().save(path)
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [] and os.path.exists(path)


def test_first_save_takes_no_backup(tmp_path):
    path = tmp_path / "herald_state.json"
    _saved(path, 1)
    assert backup_paths(str(path)) == []


def test_save_backs_up_the_previous_file_byte_for_byte(tmp_path):
    path = tmp_path / "herald_state.json"
    _saved(path, 1)
    first = path.read_bytes()

    _saved(path, 2)

    backups = backup_paths(str(path))
    assert len(backups) == 1
    assert re.fullmatch(r"herald_state\.json\.bak\.\d{8}T\d{12}Z", os.path.basename(backups[0]))
    with open(backups[0], "rb") as f:
        assert f.read() == first
    assert HeraldState.load(str(path)).last_scored_epoch == 2


def test_backups_rotate_to_the_newest_n(tmp_path, monkeypatch):
    monkeypatch.setenv(BACKUP_KEEP_ENV, "3")
    path = tmp_path / "herald_state.json"
    for epoch in range(6):
        _saved(path, epoch)

    assert [_epoch_of(b) for b in backup_paths(str(path))] == [2, 3, 4]
    assert _epoch_of(path) == 5


def test_default_keeps_fourteen_backups(tmp_path):
    assert DEFAULT_BACKUP_KEEP == 14
    path = tmp_path / "herald_state.json"
    for epoch in range(17):
        _saved(path, epoch)

    assert [_epoch_of(b) for b in backup_paths(str(path))] == list(range(2, 16))


def test_backups_in_the_same_microsecond_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "_utc_stamp", lambda: "20260911T000000000000Z")
    path = tmp_path / "herald_state.json"
    for epoch in range(4):
        _saved(path, epoch)

    backups = backup_paths(str(path))
    assert [os.path.basename(b) for b in backups] == [
        "herald_state.json.bak.20260911T000000000000Z",
        "herald_state.json.bak.20260911T000000000000Z-1",
        "herald_state.json.bak.20260911T000000000000Z-2",
    ]
    assert [_epoch_of(b) for b in backups] == [0, 1, 2]


def test_backup_keep_zero_disables_backups_and_deletes_none(tmp_path, monkeypatch):
    path = tmp_path / "herald_state.json"
    for epoch in range(3):
        _saved(path, epoch)
    before = backup_paths(str(path))
    assert len(before) == 2

    monkeypatch.setenv(BACKUP_KEEP_ENV, "0")
    for epoch in range(3, 6):
        _saved(path, epoch)

    assert backup_paths(str(path)) == before
    assert _epoch_of(path) == 5


def test_rotation_only_prunes_its_own_backups(tmp_path, monkeypatch):
    monkeypatch.setenv(BACKUP_KEEP_ENV, "1")
    others = [
        "herald_state.json.corrupt.20200101T000000000000Z",
        "herald_state.json.bak.manual",
        "herald_state.json.bak.20200101T000000000000Z.keep",
        "herald_state.json.bak.20200101T000000000000Z.kept",
        "herald_state.json.schema3.20200101T000000000000Z",
        "other.json.bak.20200101T000000000000Z",
    ]
    for name in others:
        (tmp_path / name).write_text("keep me")
    path = tmp_path / "herald_state.json"
    for epoch in range(4):
        _saved(path, epoch)

    for name in others:
        assert (tmp_path / name).read_text() == "keep me"
    assert [_epoch_of(b) for b in backup_paths(str(path))] == [2]


def test_failed_save_leaves_previous_file_untouched_and_takes_no_backup(tmp_path):
    path = tmp_path / "herald_state.json"
    _saved(path, 1)
    before = path.read_bytes()
    broken = HeraldState.fresh()
    broken.pool_spent = {"brief": object()}

    with pytest.raises(TypeError):
        broken.save(str(path))

    assert path.read_bytes() == before
    assert backup_paths(str(path)) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["herald_state.json"]
