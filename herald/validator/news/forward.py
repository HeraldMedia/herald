import time

import bittensor as bt
import numpy as np

from herald.protocol import ClaimSynapse
from herald.utils.uids import get_all_uids
from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.config import (
    EPOCH_LEN,
    SLASH_COOLDOWN_EPOCHS,
    VALIDATOR_STEPS_INTERVAL,
    VALIDATOR_WAIT,
    VEST_EPOCHS,
)
from .commit_index import CommitIndex
from .fetch import fetch
from .registry import load_registry
from .reward import winning_articles
from .slashing import SlashLedger
from .vesting import VestingLedger


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


def _ledgers(self):
    if not hasattr(self, "_commit_index"):
        self._commit_index = CommitIndex(epoch_len=EPOCH_LEN)
        self._vesting = VestingLedger(vest_epochs=VEST_EPOCHS)
        self._slash = SlashLedger()
    return self._commit_index, self._vesting, self._slash


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

        commit_index, vesting, slash = _ledgers(self)
        registry = load_registry()
        block = self.subtensor.get_current_block()
        epoch = block // EPOCH_LEN
        commitments = self.subtensor.get_all_commitments(self.config.netuid)
        commit_index.observe(block, commitments)

        uids = get_all_uids(self)
        pos = {uid: i for i, uid in enumerate(uids)}
        hotkey_by_uid = {uid: self.metagraph.hotkeys[uid] for uid in uids}
        alpha_stake_by_uid = {uid: float(self.metagraph.alpha_stake[uid]) for uid in uids}
        claims_by_uid = await collect_claims(self, uids)

        winners = winning_articles(
            claims_by_uid, commitments, commit_index,
            hotkey_by_uid, alpha_stake_by_uid, briefs, registry,
        )
        for w in winners:
            vesting.start(w.article_id, w.uid, w.usd, w.url, w.hotkey)

        rewards = np.zeros(len(uids), dtype=np.float32)
        for article_id in list(vesting.active_article_ids()):
            entry = vesting.entry(article_id)
            alive = fetch(entry.url).ok
            installment, clawed_back = vesting.release(article_id, alive)
            if clawed_back:
                slash.slash(entry.hotkey, epoch + SLASH_COOLDOWN_EPOCHS)
            elif installment and entry.uid in pos:
                rewards[pos[entry.uid]] += installment

        for uid in uids:
            if slash.is_slashed(hotkey_by_uid[uid], epoch):
                rewards[pos[uid]] = 0.0

        for uid, reward in zip(uids, rewards):
            if reward:
                bt.logging.info(f"UID {uid}: ${reward:.2f} (vested)")
        self.update_scores(rewards, uids)
    except Exception as e:
        bt.logging.error(f"Error in Herald forward pass: {e}")

    time.sleep(VALIDATOR_WAIT)
