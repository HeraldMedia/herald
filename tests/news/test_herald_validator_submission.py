import os
import re
from types import SimpleNamespace

import numpy as np
import pytest

from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron
from herald.validator.news.state import (
    ALLOW_FRESH_ON_CORRUPT_ENV,
    HeraldState,
    HeraldStateLoadError,
)
from neurons.validator import Validator

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _validator(tmp_path, *, scored_epoch=10, weight_epoch=9, scores=(0.0, 1.0)):
    validator = object.__new__(Validator)
    validator.config = SimpleNamespace(
        neuron=SimpleNamespace(full_path=str(tmp_path)),
    )
    validator.scores = np.asarray(scores, dtype=np.float32)
    validator.herald_state = HeraldState.fresh()
    validator.herald_state.last_scored_epoch = scored_epoch
    validator.herald_state.last_weight_epoch = weight_epoch
    return validator


def _chain_gates(monkeypatch, pending=False):
    """Pass the chain-age gate and record every pending-commit check (a chain read)."""
    reads = []
    monkeypatch.setattr(BaseNeuron, "should_set_weights", lambda self: True)
    monkeypatch.setattr(BaseValidatorNeuron, "_check_pending_weight_commit",
                        lambda self: reads.append(True) or pending)
    return reads


def test_scored_epoch_can_be_submitted_once(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    _chain_gates(monkeypatch)

    assert validator.should_set_weights() is True

    validator.herald_state.last_weight_epoch = 10
    assert validator.should_set_weights() is False


def test_empty_score_vector_is_not_marked_for_submission(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scores=(0.0, 0.0))
    _chain_gates(monkeypatch)

    assert validator.should_set_weights() is False


def test_nothing_to_submit_never_reaches_the_pending_commit_check(tmp_path, monkeypatch):
    # A failing RPC check retries with sleeps, counts a suppression and eventually logs
    # WEIGHT_SUBMISSION_STALLED. None of that may happen for an epoch Herald would not submit.
    reads = _chain_gates(monkeypatch)
    for validator in (_validator(tmp_path, scored_epoch=10, weight_epoch=10),
                      _validator(tmp_path, scores=(0.0, 0.0))):
        assert validator.should_set_weights() is False
        assert getattr(validator, "_suppressed_weight_submissions", 0) == 0
    assert reads == []


def test_an_epoch_to_submit_still_waits_on_a_pending_commit(tmp_path, monkeypatch):
    reads = _chain_gates(monkeypatch, pending=True)
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=9)

    assert validator.should_set_weights() is False
    assert reads == [True]


def test_successful_submission_persists_scored_epoch(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    monkeypatch.setattr(BaseValidatorNeuron, "set_weights", lambda self: True)

    assert validator.set_weights() is True
    assert validator.herald_state.last_weight_epoch == 10
    assert HeraldState.load(str(tmp_path / "herald_state.json")).last_weight_epoch == 10


def test_failed_submission_does_not_advance_epoch(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    monkeypatch.setattr(BaseValidatorNeuron, "set_weights", lambda self: False)

    assert validator.set_weights() is False
    assert validator.herald_state.last_weight_epoch == 9


def test_last_weight_epoch_has_exactly_the_documented_writer():
    # state.py documents Validator.set_weights as the only writer (besides initialisation); keep
    # that documented statement true.
    writers = set()
    for top in ("herald", "neurons", "scripts", "core"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if re.search(r"\.last_weight_epoch\s*=(?!=)", line):
                            writers.add((os.path.relpath(path, ROOT), line.strip()))
    assert writers == {
        ("herald/validator/news/state.py", "self.last_weight_epoch = last_weight_epoch"),
        ("neurons/validator.py", "state.last_weight_epoch = state.last_scored_epoch"),
    }


def _patch_startup(tmp_path, monkeypatch):
    def base_init(self, config=None):
        self.config = SimpleNamespace(
            neuron=SimpleNamespace(full_path=str(tmp_path), disable_set_weights=True),
        )
        self.uid = 0

    monkeypatch.setattr(BaseValidatorNeuron, "__init__", base_init)
    monkeypatch.setattr("neurons.validator.get_cloudwatch_handler", lambda **kwargs: None)
    monkeypatch.delenv(ALLOW_FRESH_ON_CORRUPT_ENV, raising=False)


def test_validator_loads_herald_state_at_startup(tmp_path, monkeypatch):
    saved = HeraldState.fresh()
    saved.last_scored_epoch = 42
    saved.save(str(tmp_path / "herald_state.json"))
    _patch_startup(tmp_path, monkeypatch)

    assert Validator().herald_state.last_scored_epoch == 42


def test_validator_refuses_to_start_on_an_unreadable_state_file(tmp_path, monkeypatch):
    (tmp_path / "herald_state.json").write_text("{ not json")
    _patch_startup(tmp_path, monkeypatch)

    with pytest.raises(HeraldStateLoadError, match="Refusing to start"):
        Validator()
