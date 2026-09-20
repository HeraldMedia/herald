from types import SimpleNamespace

from herald.base.miner import BaseMinerNeuron
from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron


def test_core_metagraph_sync_omits_unused_extra_info():
    calls = []
    metagraph = SimpleNamespace(
        _assign_neurons=lambda block, lite, subtensor: calls.append(
            ("neurons", block, lite, subtensor)
        ),
        _set_metagraph_attributes=lambda block: calls.append(("attributes", block)),
        _get_all_stakes_from_chain=lambda block: calls.append(("stakes", block)),
        _apply_extra_info=lambda block: calls.append(("extra", block)),
    )
    subtensor = SimpleNamespace(get_current_block=lambda: 123)
    neuron = SimpleNamespace(subtensor=subtensor, metagraph=metagraph)

    BaseNeuron.sync_core_metagraph(neuron)

    assert calls == [
        ("neurons", 123, True, subtensor),
        ("attributes", 123),
        ("stakes", 123),
    ]


class ConcreteValidator(BaseValidatorNeuron):
    async def forward(self):
        return None

    def run(self):
        return None


class ConcreteMiner(BaseMinerNeuron):
    async def forward(self, synapse):
        return synapse

    def blacklist(self, synapse):
        return False, ""

    def priority(self, synapse):
        return 0.0

    def run(self):
        return None


def test_validator_setup_serves_no_axon(tmp_path, monkeypatch):
    """A Herald validator answers no requests, so it builds no axon and publishes no address."""

    def fake_base_init(self, config=None):
        self.config = config
        self.metagraph = SimpleNamespace(hotkeys=["hk0"], n=1)
        self.wallet = object()
        self.step = 0

    def no_axon(*args, **kwargs):
        raise AssertionError("a validator must not build an axon")

    monkeypatch.setattr(BaseNeuron, "__init__", fake_base_init)
    monkeypatch.setattr("herald.base.validator.bt.Dendrite", lambda wallet: object())
    monkeypatch.setattr("herald.base.validator.bt.Axon", no_axon)
    monkeypatch.setattr("herald.base.validator.asyncio.get_event_loop", lambda: object())
    monkeypatch.setattr(ConcreteValidator, "sync", lambda self: None)

    # A config without an axon_off switch: setting up must not consult one.
    config = SimpleNamespace(neuron=SimpleNamespace(full_path=str(tmp_path)))
    validator = ConcreteValidator(config=config)

    assert not hasattr(validator, "axon")
    assert not hasattr(validator, "serve_axon")
    assert not hasattr(BaseValidatorNeuron, "serve_axon")


def test_miner_setup_still_attaches_its_axon(monkeypatch):
    """Only the validator stopped serving; a miner still answers validators on its axon."""
    axon = {}

    class FakeAxon:
        def __init__(self, wallet=None, config=None):
            axon["built"] = True

        def attach(self, forward_fn=None, blacklist_fn=None, priority_fn=None):
            axon["handlers"] = (forward_fn, blacklist_fn, priority_fn)
            return self

    def fake_base_init(self, config=None):
        self.config = config
        self.wallet = object()

    monkeypatch.setattr(BaseNeuron, "__init__", fake_base_init)
    monkeypatch.setattr("herald.base.miner.bt.Axon", FakeAxon)

    config = SimpleNamespace(
        blacklist=SimpleNamespace(force_validator_permit=True, allow_non_registered=False)
    )
    miner = ConcreteMiner(config=config)

    assert axon["built"] is True
    assert axon["handlers"] == (miner.forward, miner.blacklist, miner.priority)
