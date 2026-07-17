from types import SimpleNamespace

import numpy as np

from herald.base.validator import BaseValidatorNeuron
from herald.validator.news.state import HeraldState
from neurons.validator import Validator


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


def test_scored_epoch_can_be_submitted_once(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    monkeypatch.setattr(BaseValidatorNeuron, "should_set_weights", lambda self: True)

    assert validator.should_set_weights() is True

    validator.herald_state.last_weight_epoch = 10
    assert validator.should_set_weights() is False


def test_empty_score_vector_is_not_marked_for_submission(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scores=(0.0, 0.0))
    monkeypatch.setattr(BaseValidatorNeuron, "should_set_weights", lambda self: True)

    assert validator.should_set_weights() is False


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
