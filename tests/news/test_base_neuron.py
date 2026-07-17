from types import SimpleNamespace

from herald.base.neuron import BaseNeuron


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
