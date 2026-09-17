import importlib
from types import SimpleNamespace

import numpy as np
import pytest

import neurons.validator as valmod
from herald.base.validator import BaseValidatorNeuron
from herald.validator.news import forward as fwd
from herald.validator.news.state import HeraldState
from neurons.validator import Validator


def _validator(tmp_path, *, scored_epoch=10, weight_epoch=9, scores=(0.0, 1.0),
               block=1000, last_update=(0, 700), pending=False, epoch_length=100):
    validator = object.__new__(Validator)
    validator.step = 1
    validator.uid = 1
    validator.config = SimpleNamespace(
        netuid=69,
        neuron=SimpleNamespace(
            full_path=str(tmp_path), disable_set_weights=False, epoch_length=epoch_length,
        ),
    )
    validator.scores = np.asarray(scores, dtype=np.float32)
    validator.herald_state = HeraldState.fresh()
    validator.herald_state.last_scored_epoch = scored_epoch
    validator.herald_state.last_weight_epoch = weight_epoch
    # fake metagraph: last_update[uid] is the chain's record of this validator's weights
    validator.metagraph = SimpleNamespace(
        n=2, uids=np.array([0, 1]), last_update=np.array(last_update),
    )
    validator.wallet = SimpleNamespace(
        hotkey=SimpleNamespace(ss58_address="validator-hotkey")
    )
    # fake subtensor: commit-reveal is on, with or without a commit of ours awaiting reveal
    validator.subtensor = SimpleNamespace(
        commit_reveal_enabled=lambda netuid: True,
        get_timelocked_weight_commits=lambda netuid: (
            [("validator-hotkey", block - 10, "encrypted", 1)] if pending else []
        ),
    )
    validator._block = block
    return validator


@pytest.fixture(autouse=True)
def _fake_block(monkeypatch):
    monkeypatch.setattr(Validator, "block", property(lambda self: self._block), raising=False)


def test_stale_weight_record_is_resubmitted_within_the_same_epoch(tmp_path, monkeypatch):
    # already submitted this epoch, the chain's record for our uid is older than the interval
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=10,
                           block=1000, last_update=(0, 700))
    monkeypatch.setattr(valmod, "WEIGHT_RESUBMIT_BLOCKS", 180)

    assert validator.should_set_weights() is True


def test_fresh_weight_record_is_not_resubmitted(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=10,
                           block=1000, last_update=(0, 880))
    monkeypatch.setattr(valmod, "WEIGHT_RESUBMIT_BLOCKS", 180)
    messages = []
    monkeypatch.setattr(valmod.bt.logging, "info", messages.append)

    assert validator.should_set_weights() is False
    assert any("skipping resubmission" in message for message in messages)
    assert any("120 blocks old" in message for message in messages)


def test_empty_score_vector_is_not_marked_for_submission(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scores=(0.0, 0.0))
    messages = []
    monkeypatch.setattr(valmod.bt.logging, "info", messages.append)

    assert validator.should_set_weights() is False
    assert any("no rewarded miners" in message for message in messages)


def test_pending_timelocked_commit_suppresses_resubmission(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=10,
                           block=1000, last_update=(0, 700), pending=True)
    monkeypatch.setattr(valmod, "WEIGHT_RESUBMIT_BLOCKS", 180)

    assert validator.should_set_weights() is False


def test_unreadable_weight_record_skips_instead_of_raising(tmp_path, monkeypatch):
    validator = _validator(tmp_path, scored_epoch=10, weight_epoch=10)
    monkeypatch.setattr(BaseValidatorNeuron, "should_set_weights", lambda self: True)
    validator.metagraph = SimpleNamespace(last_update=None)

    assert validator.should_set_weights() is False


def test_resubmit_interval_comes_from_the_environment_and_defaults_to_180(monkeypatch):
    from herald.validator.utils import config as cfg

    monkeypatch.delenv("HERALD_WEIGHT_RESUBMIT_BLOCKS", raising=False)
    assert importlib.reload(cfg).WEIGHT_RESUBMIT_BLOCKS == 180

    monkeypatch.setenv("HERALD_WEIGHT_RESUBMIT_BLOCKS", "360")
    assert importlib.reload(cfg).WEIGHT_RESUBMIT_BLOCKS == 360

    monkeypatch.delenv("HERALD_WEIGHT_RESUBMIT_BLOCKS")
    assert importlib.reload(cfg).WEIGHT_RESUBMIT_BLOCKS == 180


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


@pytest.mark.asyncio
async def test_scoring_still_runs_once_per_epoch(tmp_path, monkeypatch):
    # Resubmission is a chain-cadence concern only: an epoch already scored must not be scored
    # again, however often its weights are published.
    monkeypatch.setattr(fwd.time, "sleep", lambda *_: None)
    errors = []
    monkeypatch.setattr(fwd.bt.logging, "error", errors.append)
    briefs_calls = []
    monkeypatch.setattr(fwd, "get_briefs", lambda now=None: briefs_calls.append(1) or [])

    block = 10 * fwd.VEST_EPOCH_LEN + fwd.HERALD_EPOCH_LAG
    state = HeraldState.fresh()
    state.last_scored_epoch = 10
    neuron = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69, neuron=SimpleNamespace(full_path=str(tmp_path))),
        subtensor=SimpleNamespace(get_current_block=lambda: block),
        herald_state=state,
    )

    await fwd.forward(neuron)

    assert errors == []                # the pass returned early, it did not fail
    assert briefs_calls == []          # epoch 10 already scored: no second scoring pass
    assert state.last_scored_epoch == 10
