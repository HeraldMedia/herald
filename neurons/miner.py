import os
import time
import typing
import threading
import random

import bittensor as bt

from herald.base.miner import BaseMinerNeuron
from herald.miner.claim_store import ClaimStore
from herald.protocol import ClaimRecord, ClaimSynapse
from core.auto_update import run_auto_update


class Miner(BaseMinerNeuron):
    """Serves the miner's active article claims when a validator pulls them."""

    def __init__(self, config=None):
        super(Miner, self).__init__(config=config)
        store_path = os.getenv(
            "HERALD_CLAIM_STORE",
            os.path.join(self.config.neuron.full_path, "claims.json"),
        )
        self.claim_store = ClaimStore(store_path)
        if self.config.dev_mode:
            bt.logging.info("DEV MODE ENABLED")

    async def forward(self, synapse: ClaimSynapse) -> ClaimSynapse:
        records = self.claim_store.active_claims()
        if synapse.request_brief_ids:
            wanted = set(synapse.request_brief_ids)
            records = [r for r in records if r["brief_id"] in wanted]
        synapse.claims = [
            ClaimRecord(
                brief_id=r["brief_id"],
                target_outlet_id=r["target_outlet_id"],
                article_url=r["article_url"],
                claimer_hotkey=r["claimer_hotkey"],
                nonce=r["nonce"],
                bond_atto=r["bond_atto"],
                version_id=r["version_id"],
            )
            for r in records
        ]
        return synapse

    async def blacklist(self, synapse: ClaimSynapse) -> typing.Tuple[bool, str]:
        if self.config.dev_mode:
            return False, "Blacklist disabled in dev mode"

        if synapse.failed_verification:
            return True, "Signature verification failed"

        signature = synapse.dendrite.signature if synapse.dendrite else None
        if not signature or not isinstance(signature, str) or signature.lower().strip() in (
            "null", "none", "false", "0", "undefined", ""
        ):
            return True, "Missing required signature"

        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return True, "Missing dendrite or hotkey"

        if (
            not self.config.blacklist.allow_non_registered
            and synapse.dendrite.hotkey not in self.metagraph.hotkeys
        ):
            return True, "Unrecognized hotkey"

        uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        if self.config.blacklist.force_validator_permit:
            if not self.metagraph.validator_permit[uid] or self.metagraph.S[uid] < self.config.blacklist.min_stake:
                return True, "Non-validator hotkey"

        return False, "Hotkey recognized!"

    async def priority(self, synapse: ClaimSynapse) -> float:
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return 0.0
        caller_uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        return float(self.metagraph.S[caller_uid])


def auto_update_loop(config):
    while True:
        if not config.neuron.disable_auto_update:
            run_auto_update('miner')
        time.sleep(random.randint(600, 900))


if __name__ == "__main__":
    with Miner() as miner:
        update_thread = threading.Thread(target=auto_update_loop, args=(miner.config,), daemon=True)
        update_thread.start()

        while True:
            bt.logging.info(f"Miner running... {time.time()}")
            time.sleep(5)
