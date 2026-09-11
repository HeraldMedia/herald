"""Rollback rehearsal for herald_state.json.

A file written by this code must still load under the previous release's loader, and a legacy
(schema 1) file must survive forward-migrate -> save -> roll back -> load without loss.

The previous release's modules are frozen verbatim from BASE_COMMIT under
fixtures/state_rollback/legacy and materialised into a throwaway package so both versions run side
by side. The legacy file is a synthetic schema-1 state of the same shape.
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

from herald.validator.news.state import SCHEMA_VERSION, HeraldState, backup_paths
from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
FIXTURES = os.path.join(HERE, "fixtures", "state_rollback")
LEGACY_SOURCES = os.path.join(FIXTURES, "legacy")
LEGACY_FILE = os.path.join(FIXTURES, "legacy_schema1_state.json")
BASE_COMMIT = "4d8ee079ff0f16c38de2a66a5ff2bc71f60c577e"
MODULES = ("state", "vesting", "commit_index", "disputes", "slashing")


@pytest.fixture
def legacy(tmp_path):
    """The previous release's ``state`` module, imported from a temp package built from the frozen sources."""
    name = f"herald_legacy_state_{uuid.uuid4().hex}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    for module in MODULES:
        shutil.copyfile(os.path.join(LEGACY_SOURCES, f"{module}.py.txt"), package / f"{module}.py")
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module(f"{name}.state")
    finally:
        sys.path.remove(str(tmp_path))
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]


def _git_show(path):
    try:
        return subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", REPO_ROOT, "show", f"{BASE_COMMIT}:{path}"],
            capture_output=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _legacy_file():
    with open(LEGACY_FILE, encoding="utf-8") as f:
        return json.load(f)


def _with_config_divisors(data):
    """epoch_len / vest_epochs always come from config on load, never from the file."""
    out = json.loads(json.dumps(data))
    out["commit_index"]["epoch_len"] = EPOCH_LEN
    out["vesting"]["vest_epochs"] = VEST_EPOCHS
    return out


def _without_schema(data):
    return {k: v for k, v in data.items() if k != "schema_version"}


def _populated_state():
    s = HeraldState.fresh()
    s.commit_index.observe({"hkA": ("HRLD1|aa", 100), "hkB": ("HRLD1|bb", 250)})
    s.vesting.start("art1", uid=1, total_usd=500.0, url="https://example.com/a", hotkey="hkA",
                    brief_id="b1", commit_epoch=3, start_epoch=4, outlet_id="reuters", tier=1,
                    attribution=2, reveal={"nonce": "n1", "target_outlet_id": "reuters"})
    s.vesting.start("art2", uid=2, total_usd=90.0, url="https://example.com/b", hotkey="hkB",
                    brief_id="b1", start_epoch=4)
    s.vesting.release("art1", epoch=4)
    s.vesting.clawback("art2")
    s.slash.slash("hkB", until_epoch=11)
    s.disputes.open("art1", "hkD", 5)
    s.pool_spent = {"b1": 16.5}
    s.last_scored_epoch = 5
    s.last_weight_epoch = 4
    return s


@pytest.mark.parametrize("module", MODULES)
def test_frozen_legacy_sources_match_the_base_commit(module):
    blob = _git_show(f"herald/validator/news/{module}.py")
    if blob is None:
        pytest.skip("git history for the base commit is not available in this checkout")
    with open(os.path.join(LEGACY_SOURCES, f"{module}.py.txt"), "rb") as f:
        assert f.read() == blob


def test_legacy_package_really_is_the_previous_loader(legacy, tmp_path):
    assert "schema_version" not in legacy.HeraldState.fresh().to_dict()
    path = tmp_path / "herald_state.json"
    path.write_text("{ corrupt")
    assert legacy.HeraldState.load(str(path)).last_scored_epoch == -1  # legacy loader: fresh state


def test_legacy_file_forward_migrate_save_roll_back_load(legacy, tmp_path):
    original = _with_config_divisors(_legacy_file())
    path = tmp_path / "herald_state.json"
    shutil.copyfile(LEGACY_FILE, path)
    original_bytes = path.read_bytes()

    # 1. Forward: the new code loads the legacy (schema 1) file and saves it as schema 2, keeping the
    #    untouched original as a dated backup.
    migrated = HeraldState.load(str(path))
    assert migrated.last_scored_epoch == 12
    migrated.save(str(path))
    written = json.loads(path.read_text())
    assert written["schema_version"] == SCHEMA_VERSION
    assert _without_schema(written) == original
    backups = backup_paths(str(path))
    assert len(backups) == 1
    with open(backups[0], "rb") as f:
        assert f.read() == original_bytes

    # 2. Roll back: the legacy from_dict raises on anything it cannot parse (only its load()
    #    swallows errors), so a clean parse here proves the schema-2 file is readable by it.
    rolled_back = legacy.HeraldState.from_dict(json.loads(path.read_text()))
    assert rolled_back.to_dict() == original
    assert legacy.HeraldState.load(str(path)).to_dict() == original
    rolled_back.save(str(path))  # the rolled-back validator writes its own legacy format
    assert "schema_version" not in json.loads(path.read_text())

    # 3. Roll forward again: nothing was lost across the round trip.
    assert _without_schema(HeraldState.load(str(path)).to_dict()) == original


def test_populated_ledger_written_by_new_code_loads_under_the_legacy_loader(legacy, tmp_path):
    path = tmp_path / "herald_state.json"
    state = _populated_state()
    state.save(str(path))

    old = legacy.HeraldState.from_dict(json.loads(path.read_text()))

    assert old.to_dict() == _without_schema(state.to_dict())
    assert old.vesting.active_article_ids() == ["art1"]
    assert old.vesting.entry("art1").reveal == {"nonce": "n1", "target_outlet_id": "reuters"}
    assert old.slash.is_slashed("hkB", 10) and old.disputes.is_disputed("art1")
    assert (old.last_scored_epoch, old.last_weight_epoch, old.pool_spent) == (5, 4, {"b1": 16.5})


def test_unknown_vest_field_breaks_the_legacy_loader_but_not_this_one(legacy, tmp_path):
    data = _populated_state().to_dict()
    data["schema_version"] = SCHEMA_VERSION + 1
    data["vesting"]["entries"]["art1"]["future_int_field"] = 7_000_000
    path = tmp_path / "herald_state.json"
    path.write_text(json.dumps(data))

    with pytest.raises(TypeError):
        legacy.HeraldState.from_dict(data)
    assert legacy.HeraldState.load(str(path)).vesting.active_article_ids() == []  # legacy loader: fresh state

    loaded = HeraldState.load(str(path))
    assert loaded.vesting.active_article_ids() == ["art1"]
    assert loaded.vesting.entry("art1").total_usd == 500.0
    assert loaded.slash.is_slashed("hkB", 10) and loaded.disputes.is_disputed("art1")
