from types import SimpleNamespace

import numpy as np
import pytest

from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron


class ConcreteValidator(BaseValidatorNeuron):
    async def forward(self):
        return None

    def run(self):
        return None


def test_constructor_restores_scores_before_initial_sync(tmp_path, monkeypatch):
    np.savez(
        tmp_path / "state.npz",
        step=7,
        scores=np.array([0.25, 0.75], dtype=np.float32),
        hotkeys=np.array(["hk0", "hk1"]),
        spec_version=BaseValidatorNeuron.spec_version,
    )
    config = SimpleNamespace(neuron=SimpleNamespace(full_path=str(tmp_path), axon_off=True))
    metagraph = SimpleNamespace(hotkeys=["hk0", "hk1"], n=2)

    def fake_base_init(self, config=None):
        self.config = config
        self.metagraph = metagraph
        self.wallet = object()
        self.step = 0

    seen = {}
    monkeypatch.setattr(BaseNeuron, "__init__", fake_base_init)
    monkeypatch.setattr("herald.base.validator.bt.Dendrite", lambda wallet: object())
    monkeypatch.setattr("herald.base.validator.asyncio.get_event_loop", lambda: object())
    monkeypatch.setattr(
        ConcreteValidator,
        "sync",
        lambda self: seen.update(step=self.step, scores=self.scores.copy()),
    )

    validator = ConcreteValidator(config=config)

    assert seen["step"] == 7
    assert seen["scores"] == pytest.approx([0.25, 0.75])
    assert validator.hotkeys == ["hk0", "hk1"]


@pytest.mark.parametrize("saved_spec", [None, 2061])
def test_load_state_discards_unversioned_or_old_scores(tmp_path, saved_spec):
    fields = {
        "step": 7,
        "scores": np.array([0.25, 0.75], dtype=np.float32),
        "hotkeys": np.array(["hk0", "hk1"]),
    }
    if saved_spec is not None:
        fields["spec_version"] = saved_spec
    np.savez(tmp_path / "state.npz", **fields)

    validator = object.__new__(ConcreteValidator)
    validator.config = SimpleNamespace(neuron=SimpleNamespace(full_path=str(tmp_path)))
    validator.metagraph = SimpleNamespace(hotkeys=["hk0", "hk1"], n=2)

    validator.load_state()

    assert validator.step == 0
    assert validator.scores.tolist() == [0.0, 0.0]
    assert validator.hotkeys == ["hk0", "hk1"]


def test_save_state_records_current_spec_version(tmp_path):
    validator = object.__new__(ConcreteValidator)
    validator.config = SimpleNamespace(neuron=SimpleNamespace(full_path=str(tmp_path)))
    validator.step = 3
    validator.scores = np.array([0.0, 1.0], dtype=np.float32)
    validator.hotkeys = ["hk0", "hk1"]

    validator.save_state()

    state = np.load(tmp_path / "state.npz", allow_pickle=False)
    assert int(state["spec_version"]) == BaseValidatorNeuron.spec_version
