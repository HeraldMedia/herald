import time
import os
import wandb
import threading
import bittensor as bt
import random
import numpy as np

from herald.base.validator import BaseValidatorNeuron
from herald.validator.news.forward import _state, _state_path, forward
from herald.validator.news.publish import publish_weight_receipt
from herald.validator.utils.config import __version__, WANDB_PROJECT
from herald.utils.cloudwatch_logging import get_cloudwatch_handler
from core.auto_update import run_auto_update

class Validator(BaseValidatorNeuron):
    """
    Your validator neuron class. You should use this class to define your validator's behavior. In particular, you should replace the forward function with your own logic.

    This class inherits from the BaseValidatorNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a validator such as keeping a moving average of the scores of the miners and using them to set weights at the end of each epoch. Additionally, the scores are reset for new hotkeys at the end of each epoch.
    """

    def __init__(self, config=None):
        super(Validator, self).__init__(config=config)

        try:
            cw_handler = get_cloudwatch_handler(
                log_group="/herald/validator",
                stream_name=f"validator-uid-{self.uid}",
            )
            if cw_handler:
                bt.logging._logger.addHandler(cw_handler)
                bt.logging.info("CloudWatch logging enabled")
        except Exception as e:
            bt.logging.warning(f"Failed to set up CloudWatch logging: {e}")

        # Initialize wandb only if disable_set_weights is False
        if not self.config.neuron.disable_set_weights:
            try:
                wandb.init(
                    entity="herald_network",
                    project=WANDB_PROJECT,
                    name=f"validator-{self.uid}-{__version__}",
                    config=self.config,
                    reinit="finish_previous"
                )
            except Exception as e:
                bt.logging.error(f"Failed to initialize wandb run: {e}")

    async def forward(self):
        """
        Validator forward pass. Consists of:
        - Generating the query
        - Querying the miners
        - Getting the responses
        - Rewarding the miners
        - Updating the scores
        """
        return await forward(self)

    def should_set_weights(self) -> bool:
        if not super().should_set_weights():
            return False

        state = _state(self)
        if state.last_weight_epoch >= state.last_scored_epoch:
            bt.logging.info(
                f"Herald epoch {state.last_scored_epoch} was already submitted; "
                "waiting for the next daily evaluation"
            )
            return False

        scores = np.asarray(self.scores)
        if not np.any(np.isfinite(scores) & (scores > 0)):
            bt.logging.info(
                f"Herald epoch {state.last_scored_epoch} has no rewarded miners; "
                "skipping weight submission"
            )
            return False
        return True

    def set_weights(self) -> bool:
        submitted = super().set_weights()
        if submitted is not True:
            return False

        state = _state(self)
        state.last_weight_epoch = state.last_scored_epoch
        path = _state_path(self)
        if path:
            state.save(path)
        endpoint = os.getenv("HERALD_RESULTS_ENDPOINT")
        if endpoint and getattr(self, "_last_submitted_weight_vector_hash", None):
            network = getattr(self.subtensor, "network", None) or getattr(
                getattr(self.config, "subtensor", None), "network", "unknown"
            )
            try:
                pending = bool(self.subtensor.commit_reveal_enabled(self.config.netuid))
            except Exception:
                pending = True
            publish_weight_receipt(endpoint, {
                "schema_version": 1, "network": str(network),
                "netuid": int(self.config.netuid), "epoch": int(state.last_scored_epoch),
                "chain_block": int(self.block),
                "validator_hotkey": self.wallet.hotkey.ss58_address,
                "vector_hash": self._last_submitted_weight_vector_hash,
                "status": "pending_reveal" if pending else "revealed",
            }, self.wallet.hotkey)
        return True

def auto_update_loop(config):
    while True:
        if not config.neuron.disable_auto_update:
            run_auto_update('validator')
        sleep_time = random.randint(600, 900)  # Random time between 10 and 15 minutes
        time.sleep(sleep_time)

if __name__ == "__main__":
    validator = Validator()
    update_thread = threading.Thread(target=auto_update_loop, args=(validator.config,), daemon=True)
    update_thread.start()

    # Keep validation on the supervised process's main thread. Startup failures
    # must terminate the process so PM2/systemd can restart it instead of
    # reporting a sleeping wrapper whose worker thread has already stopped.
    validator.run()
