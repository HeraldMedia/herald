"""The per-UID view of a subnet that the validator keeps between syncs.

``Metagraph.sync(block)`` reads, at one block, the subnet's neurons
(NeuronInfoRuntimeApi.get_neurons_lite) and their stakes (SubnetInfoRuntimeApi.get_subnet_state),
and lays them out by UID:

- ``n`` (0-d int64 array), ``block`` (0-d int64 array), ``uids`` (int64 array);
- ``hotkeys`` and ``coldkeys`` (lists of ss58 strings, index = UID);
- ``active`` (int64), ``last_update`` (int64 block numbers), ``validator_permit`` (bool);
- ``stake`` / ``S`` / ``total_stake``, ``alpha_stake`` and ``tao_stake`` (float32, in TAO/alpha
  units, rao / 1e9; ``tao_stake`` is weighted by the root stake weight 0.18).

When the stake read fails the neuron fields are still updated and the stake arrays keep their
previous values.
"""

import numpy as np

from herald.sdk.log import log

ROOT_TAO_STAKE_WEIGHT = 0.18
RAO_PER_TAO = 10 ** 9


def _tao(values) -> np.ndarray:
    return np.array([int(v) / RAO_PER_TAO for v in values or []], dtype=np.float32)


class Metagraph:
    def __init__(self, netuid: int, subtensor=None, lite: bool = True, sync: bool = True,
                 block=None):
        self.netuid = int(netuid)
        self.subtensor = subtensor
        self.network = getattr(subtensor, "network", None)
        self.lite = lite
        self.n = np.array(0, dtype=np.int64)
        self.block = np.array(0, dtype=np.int64)
        self.uids = np.array([], dtype=np.int64)
        self.hotkeys = []
        self.coldkeys = []
        self.active = np.array([], dtype=np.int64)
        self.last_update = np.array([], dtype=np.int64)
        self.validator_permit = np.array([], dtype=bool)
        self.stake = self.total_stake = np.array([], dtype=np.float32)
        self.alpha_stake = np.array([], dtype=np.float32)
        self.tao_stake = np.array([], dtype=np.float32)
        if sync:
            self.sync(block=block, lite=lite)

    @property
    def S(self) -> np.ndarray:
        return self.stake

    def __repr__(self) -> str:
        return (f"metagraph(netuid:{self.netuid}, n:{int(self.n)}, block:{int(self.block)}, "
                f"network:{self.network})")

    __str__ = __repr__

    def sync(self, block=None, lite: bool = True, subtensor=None):
        """Read the subnet at `block` (default: the current block)."""
        subtensor = subtensor or self.subtensor
        if block is None:
            block = subtensor.get_current_block()
        block = int(block)
        records = subtensor.runtime("NeuronInfoRuntimeApi", "get_neurons_lite", [self.netuid],
                                    block=block) or []
        records = sorted(records, key=lambda record: int(record["uid"]))
        self.lite = lite
        self.block = np.array(block, dtype=np.int64)
        self.n = np.array(len(records), dtype=np.int64)
        self.uids = np.array([int(r["uid"]) for r in records], dtype=np.int64)
        self.hotkeys = [str(r["hotkey"]) for r in records]
        self.coldkeys = [str(r["coldkey"]) for r in records]
        self.active = np.array([int(bool(r["active"])) for r in records], dtype=np.int64)
        self.last_update = np.array([int(r["last_update"]) for r in records], dtype=np.int64)
        self.validator_permit = np.array([bool(r["validator_permit"]) for r in records], dtype=bool)
        self._sync_stakes(subtensor, block)
        return self

    def _sync_stakes(self, subtensor, block: int):
        try:
            state = subtensor.runtime("SubnetInfoRuntimeApi", "get_subnet_state", [self.netuid],
                                      block=block)
        except Exception as e:
            log.debug(f"Unable to read the stakes of netuid {self.netuid}: {e}")
            return
        if not isinstance(state, dict):
            log.debug(f"Unable to retrieve subnet state for netuid `{self.netuid}`.")
            return
        if self.netuid == 0:
            tao = _tao(state.get("tao_stake"))
            self.stake = self.total_stake = self.alpha_stake = self.tao_stake = tao
            return
        self.alpha_stake = _tao(state.get("alpha_stake"))
        self.tao_stake = np.array(
            [int(v) / RAO_PER_TAO * ROOT_TAO_STAKE_WEIGHT for v in state.get("tao_stake") or []],
            dtype=np.float32,
        )
        self.stake = self.total_stake = _tao(state.get("total_stake"))
