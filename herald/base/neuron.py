# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import copy

from abc import ABC, abstractmethod

from herald.sdk.chain import Subtensor
from herald.sdk.keys import wallet_from_config
from herald.sdk.log import log
from herald.sdk.metagraph import Metagraph

# Sync calls set weights and also resyncs the metagraph.
from herald.utils.config import check_config, add_args, config
from herald.utils.misc import ttl_get_block
from herald import __spec_version__ as spec_version
from herald.production import validate_neuron_environment, validate_registry_anchor


class BaseNeuron(ABC):
    """
    Base class for Herald neurons. This class is abstract and should be inherited by a subclass. It contains the core logic shared by neurons.

    In addition to creating a wallet, subtensor, and metagraph, this class also handles the synchronization of the network state via a basic checkpointing mechanism based on epoch length.
    """

    neuron_type: str = "BaseNeuron"

    @classmethod
    def check_config(cls, config):
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser):
        add_args(cls, parser)

    @classmethod
    def config(cls):
        return config(cls)

    subtensor: "Subtensor"
    wallet: "Wallet"
    metagraph: "Metagraph"
    spec_version: int = spec_version

    @property
    def block(self):
        return ttl_get_block(self)

    def __init__(self, config=None):
        base_config = copy.deepcopy(config or BaseNeuron.config())
        self.config = self.config()
        self.config.merge(base_config)
        self.check_config(self.config)
        validate_neuron_environment(
            self.neuron_type,
            getattr(self.config.subtensor, "network", ""),
            self.config.netuid,
        )

        # Set up logging with the provided configuration.
        log.set_config(self.config.logging)

        # If a gpu is required, set the device to cuda:N (e.g. cuda:0)
        self.device = self.config.neuron.device

        # Log the configuration for reference.
        log.info(self.config)

        # Build the chain objects: the wallet, the chain connection and the metagraph view.
        log.info("Setting up bittensor objects.")

        # The wallet holds the cryptographic key pairs for the neuron.
        self.wallet = wallet_from_config(self.config)
        self.subtensor = Subtensor(config=self.config)
        self.metagraph = Metagraph(netuid=self.config.netuid, subtensor=self.subtensor, sync=False)
        self.sync_core_metagraph()

        log.info(f"Wallet: {self.wallet}")
        log.info(f"Subtensor: {self.subtensor}")
        log.info(f"Metagraph: {self.metagraph}")

        # Check if the miner is registered on the Bittensor network before proceeding further.
        self.check_registered()

        # Each miner gets a unique identity (UID) in the network for differentiation.
        self.uid = self.metagraph.hotkeys.index(
            self.wallet.hotkey.ss58_address
        )
        if self.neuron_type == "ValidatorNeuron":
            validate_registry_anchor(
                self.subtensor,
                self.config.netuid,
                current_block=self.subtensor.get_current_block(),
            )
        log.info(
            f"Running neuron on subnet: {self.config.netuid} with uid {self.uid} using network: {self.subtensor.chain_endpoint}"
        )
        self.step = 0

    def sync_core_metagraph(self):
        """Sync the chain fields Herald consumes (neurons and stakes), all read at one block."""
        block = self.subtensor.get_current_block()
        self.metagraph.sync(block=block, lite=True, subtensor=self.subtensor)

    @abstractmethod
    async def forward(self):
        ...

    @abstractmethod
    def run(self):
        ...

    def sync(self):
        """
        Wrapper for synchronizing the state of the network for the given miner or validator.
        """
        # Ensure miner or validator hotkey is still registered on the network.
        self.check_registered()

        if self.should_sync_metagraph():
            self.resync_metagraph()

        if self.should_set_weights():
            self.set_weights()

        # Always save state.
        self.save_state()

    def check_registered(self):
        # --- Check for registration.
        if not self.subtensor.is_hotkey_registered(
            netuid=self.config.netuid,
            hotkey_ss58=self.wallet.hotkey.ss58_address,
        ):
            log.error(
                f"Wallet: {self.wallet} is not registered on netuid {self.config.netuid}."
                f" Please register the hotkey using `btcli subnets register` before trying again"
            )
            exit()

    def should_sync_metagraph(self):
        """
        Check if enough epoch blocks have elapsed since the last checkpoint to sync.
        """
        return (
            self.block - self.metagraph.last_update[self.uid]
        ) > self.config.neuron.epoch_length

    def should_set_weights(self) -> bool:
        # Don't set weights on initialization.
        if self.step == 0:
            return False

        # Check if enough epoch blocks have elapsed since the last epoch.
        if self.config.neuron.disable_set_weights:
            return False

        # Define appropriate logic for when set weights.
        return (
            (self.block - self.metagraph.last_update[self.uid])
            > self.config.neuron.epoch_length
            and self.neuron_type != "MinerNeuron"
        )

    def save_state(self):
        log.trace(
            "save_state() not implemented for this neuron. You can implement this function to save model checkpoints or other useful data."
        )

    def load_state(self):
        log.trace(
            "load_state() not implemented for this neuron. You can implement this function to load model checkpoints or other useful data."
        )
