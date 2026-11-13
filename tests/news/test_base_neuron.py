from types import SimpleNamespace

import numpy as np

from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron
from herald.sdk.metagraph import Metagraph


def _neuron(uid, hotkey, last_update, permit=False):
    return {"uid": uid, "hotkey": hotkey, "coldkey": f"cold-{hotkey}", "active": True,
            "validator_permit": permit, "last_update": last_update}


def test_core_metagraph_sync_reads_neurons_and_stakes_at_one_block():
    calls = []

    def runtime(api, method, params, block=None):
        calls.append((api, method, tuple(params), block))
        if method == "get_neurons_lite":
            return [_neuron(1, "hk1", 90), _neuron(0, "hk0", 100, permit=True)]
        return {"alpha_stake": [2 * 10 ** 9, 0], "tao_stake": [10 ** 9, 0],
                "total_stake": [3 * 10 ** 9, 5 * 10 ** 8]}

    subtensor = SimpleNamespace(get_current_block=lambda: 123, runtime=runtime)
    metagraph = Metagraph(netuid=69, subtensor=subtensor, sync=False)
    neuron = SimpleNamespace(subtensor=subtensor, metagraph=metagraph)

    BaseNeuron.sync_core_metagraph(neuron)

    assert calls == [
        ("NeuronInfoRuntimeApi", "get_neurons_lite", (69,), 123),
        ("SubnetInfoRuntimeApi", "get_subnet_state", (69,), 123),
    ]
    assert int(metagraph.n) == 2 and int(metagraph.block) == 123
    assert metagraph.hotkeys == ["hk0", "hk1"]  # ordered by UID
    assert metagraph.uids.tolist() == [0, 1]
    assert metagraph.last_update.tolist() == [100, 90]
    assert metagraph.validator_permit.tolist() == [True, False]
    assert metagraph.S.tolist() == [3.0, 0.5]


class ConcreteValidator(BaseValidatorNeuron):
    async def forward(self):
        return None

    def run(self):
        return None


def test_validator_setup_serves_no_axon(tmp_path, monkeypatch):
    """A Herald validator answers no requests, so it builds no axon and publishes no address."""

    def fake_base_init(self, config=None):
        self.config = config
        self.metagraph = SimpleNamespace(hotkeys=["hk0"], n=1)
        self.wallet = object()
        self.step = 0

    monkeypatch.setattr(BaseNeuron, "__init__", fake_base_init)
    monkeypatch.setattr("herald.base.validator.asyncio.get_event_loop", lambda: object())
    monkeypatch.setattr(ConcreteValidator, "sync", lambda self: None)

    # A config without an axon_off switch: setting up must not consult one.
    config = SimpleNamespace(neuron=SimpleNamespace(full_path=str(tmp_path)))
    validator = ConcreteValidator(config=config)

    assert not hasattr(validator, "axon")
    assert not hasattr(validator, "dendrite")
    assert not hasattr(validator, "serve_axon")
    assert not hasattr(BaseValidatorNeuron, "serve_axon")
    assert np.asarray(validator.scores).shape == (1,)
