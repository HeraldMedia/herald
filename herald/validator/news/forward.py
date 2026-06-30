import time

import bittensor as bt
import numpy as np

from herald.protocol import ClaimSynapse
from herald.utils.uids import get_all_uids
from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.config import EPOCH_LEN, VALIDATOR_STEPS_INTERVAL, VALIDATOR_WAIT
from .commit_index import CommitIndex
from .registry import load_registry
from .reward import score_claims


async def collect_claims(self, uids):
    claims_by_uid = {}
    for uid in uids:
        try:
            responses = await self.dendrite(
                axons=[self.metagraph.axons[uid]],
                synapse=ClaimSynapse(),
                deserialize=False,
                timeout=12,
            )
            response = responses[0] if responses else None
            claims_by_uid[uid] = list(response.claims or []) if response is not None else []
        except Exception as e:
            bt.logging.warning(f"Claim query failed for UID {uid}: {e}")
            claims_by_uid[uid] = []
    return claims_by_uid


async def forward(self):
    if self.step % VALIDATOR_STEPS_INTERVAL != 0:
        time.sleep(VALIDATOR_WAIT)
        return

    bt.logging.info(f"Herald forward pass at step {self.step}")
    try:
        briefs = get_briefs()
        if not briefs:
            bt.logging.info("No active briefs; skipping scoring")
            time.sleep(VALIDATOR_WAIT)
            return

        registry = load_registry()
        commitments = self.subtensor.get_all_commitments(self.config.netuid)
        if not hasattr(self, "_commit_index"):
            self._commit_index = CommitIndex(epoch_len=EPOCH_LEN)
        self._commit_index.observe(self.subtensor.get_current_block(), commitments)

        uids = get_all_uids(self)
        hotkey_by_uid = {uid: self.metagraph.hotkeys[uid] for uid in uids}
        claims_by_uid = await collect_claims(self, uids)

        usd_by_uid = score_claims(
            claims_by_uid, commitments, self._commit_index,
            hotkey_by_uid, briefs, registry
        )
        rewards = np.array([usd_by_uid.get(uid, 0.0) for uid in uids], dtype=np.float32)
        for uid, reward in zip(uids, rewards):
            bt.logging.info(f"UID {uid}: ${reward:.2f}")
        self.update_scores(rewards, uids)
    except Exception as e:
        bt.logging.error(f"Error in Herald forward pass: {e}")

    time.sleep(VALIDATOR_WAIT)
