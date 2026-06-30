import os
import time

import bittensor as bt

from herald.protocol import ClaimSynapse
from herald.utils.uids import get_all_uids
from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.config import (
    EPOCH_LEN,
    HERALD_TOTAL_DAILY_USD,
    SLASH_COOLDOWN_EPOCHS,
    SUBNET_BURN_UID,
    VALIDATOR_STEPS_INTERVAL,
    VALIDATOR_WAIT,
)
from .chain import get_commitments_with_block
from .emission import apply_brief_caps, compute_weights
from .fetch import fetch
from .judge import judge, llm_available
from .registry import load_registry
from .publish import publish_results
from .reward import winning_articles
from .search import in_index
from .state import HeraldState


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


def _state_path(self):
    try:
        return self.config.neuron.full_path + "/herald_state.json"
    except Exception:
        return None


def _state(self) -> HeraldState:
    if not hasattr(self, "herald_state"):
        path = _state_path(self)
        self.herald_state = HeraldState.load(path) if path else HeraldState.fresh()
    return self.herald_state


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

        state = _state(self)
        commit_index, vesting, slash = state.commit_index, state.vesting, state.slash
        block = self.subtensor.get_current_block()
        epoch = block // EPOCH_LEN
        commitments_with_block = get_commitments_with_block(self.subtensor, self.config.netuid)
        commit_index.observe(commitments_with_block)
        commitments = {hk: v for hk, (v, _b) in commitments_with_block.items()}

        authority = os.getenv("HERALD_REGISTRY_AUTHORITY_HOTKEY")
        anchor_value = commitments.get(authority) if authority else None
        registry = load_registry(anchor_value)

        uids = get_all_uids(self)
        hotkey_by_uid = {uid: self.metagraph.hotkeys[uid] for uid in uids}
        alpha_stake_by_uid = {uid: float(self.metagraph.alpha_stake[uid]) for uid in uids}
        claims_by_uid = await collect_claims(self, uids)

        winners = winning_articles(
            claims_by_uid, commitments, commit_index,
            hotkey_by_uid, alpha_stake_by_uid, briefs, registry,
            fetch_fn=lambda u: fetch(u, epoch),
            search_fn=lambda u: in_index(u, epoch),
            judge_fn=judge if llm_available() else None,
        )
        for w in winners:
            vesting.start(w.article_id, w.uid, w.usd, w.url, w.hotkey, w.brief_id)

        usd_by_uid_brief = {}
        for article_id in list(vesting.active_article_ids()):
            entry = vesting.entry(article_id)
            alive = fetch(entry.url, epoch).ok
            installment, clawed_back = vesting.release(article_id, alive)
            if clawed_back:
                slash.slash(entry.hotkey, epoch + SLASH_COOLDOWN_EPOCHS)
            elif installment and entry.uid in hotkey_by_uid:
                if not slash.is_slashed(hotkey_by_uid[entry.uid], epoch):
                    key = (entry.uid, entry.brief_id)
                    usd_by_uid_brief[key] = usd_by_uid_brief.get(key, 0.0) + installment

        usd_by_uid = apply_brief_caps(usd_by_uid_brief, briefs, HERALD_TOTAL_DAILY_USD)
        weights = compute_weights(usd_by_uid, uids, HERALD_TOTAL_DAILY_USD, SUBNET_BURN_UID)
        self.update_scores(weights, uids)

        endpoint = os.getenv("HERALD_RESULTS_ENDPOINT")
        if endpoint:
            publish_results(endpoint, [{
                "article_id": w.article_id, "hotkey": w.hotkey, "brief_id": w.brief_id,
                "outlet_id": w.outlet_id, "url": w.url, "usd": w.usd,
                "status": vesting.entry(w.article_id).status,
            } for w in winners])

        path = _state_path(self)
        if path:
            state.save(path)
    except Exception as e:
        bt.logging.error(f"Error in Herald forward pass: {e}")

    time.sleep(VALIDATOR_WAIT)
