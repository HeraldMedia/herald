import os
import time

import bittensor as bt

from herald.protocol import ClaimSynapse
from herald.utils.uids import get_all_uids
from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.consensus import consensus_fingerprint
from herald.validator.utils.config import (
    HERALD_BOND_ALPHA_PER_USD,
    HERALD_DEAD_CONFIRM_EPOCHS,
    HERALD_DISPUTE_REWARD_FRACTION,
    HERALD_DISPUTE_WINDOW_EPOCHS,
    HERALD_EPOCH_LAG,
    HERALD_MAX_ARTICLES_PER_MINER,
    HERALD_MAX_PLACEMENT_DAYS,
    HERALD_REF_MODEL_ID,
    HERALD_TOTAL_DAILY_USD,
    HERALD_USE_LLM_JUDGE,
    HERALD_VEST_GRACE_EPOCHS,
    SLASH_COOLDOWN_EPOCHS,
    SLASH_MULTIPLIER,
    SUBNET_BURN_UID,
    VALIDATOR_STEPS_INTERVAL,
    VALIDATOR_WAIT,
    VEST_EPOCH_LEN,
)
from .chain import get_commitments_with_block
from .dispute_anchor import article_id_hash, parse_dispute
from .disputes import settle_persistence
from .emission import apply_reward_pools, compute_weights
from .fetch import fetch, fetch_article
from .judge import judge
from .real_news import is_paid
from .reconcile import fetch_board_results, merge_board_claims
from .registry import load_registry
from .publish import publish_results
from .reward import winning_articles
from .search import in_index
from .state import HeraldState

# Short hash of every consensus-critical tunable. Validators MUST show the same value; compare
# it across fleet logs (or the `consensus` field on published results) to catch config drift.
_CONSENSUS_FP = consensus_fingerprint()


def _persistence_status(entry, briefs_by_id, epoch, judge_fn, registry=None) -> str:
    """alive (pay), dead (clawback + slash), or hold (transient/unconfirmed — do nothing).

    Gates the per-epoch installment on LIVENESS ONLY: a reachable, non-thin page that hasn't
    turned into an ad. Topic match and search-index presence were already verified at claim time
    (snapshot-anchored, so the whole fleet agreed on them); re-checking them here on each
    validator's own live fetch only forks per-epoch pay across the fleet — search results and page
    variants legitimately differ per validator — for no slashing benefit, so they are intentionally
    NOT re-run. Clawback+slash still fire only on a CONFIRMED removal (404/410) or a confirmed swap
    to paid content, so a transient outage never slashes an honest miner.
    """
    if entry.brief_id not in briefs_by_id:
        return "hold"  # brief closed/defunded: withhold pay (its emissions burn), don't slash
    fr = fetch_article(entry.url, registry, epoch)
    if fr.status in (404, 410):
        return "dead"
    if not fr.ok:
        return "hold"  # status 0/451/5xx or thin body — geo-block/no-connect: can't confirm live
    if is_paid(entry.url, fr.text, judge_fn)[0]:
        return "dead"  # swapped to paid/sponsored content — slashable
    return "alive"


async def collect_claims(self, uids):
    # One concurrent dendrite call over all axons (bittensor gathers them), so a few slow or
    # stalling miners can't serialize into a 12s-per-uid delay that blows the scoring window.
    claims_by_uid = {uid: [] for uid in uids}
    queryable, axons = [], []
    for uid in uids:
        try:
            axons.append(self.metagraph.axons[uid])
            queryable.append(uid)
        except (KeyError, IndexError):
            continue  # uid without an axon (e.g. the burn uid): no claims to pull
    if not axons:
        return claims_by_uid
    try:
        responses = await self.dendrite(
            axons=axons, synapse=ClaimSynapse(), deserialize=False, timeout=12,
        )
    except Exception as e:
        bt.logging.warning(f"Claim query failed: {e}")
        return claims_by_uid
    for uid, response in zip(queryable, responses or []):
        try:
            claims = list(response.claims or []) if response is not None else []
        except Exception:
            claims = []
        claims_by_uid[uid] = claims[:HERALD_MAX_ARTICLES_PER_MINER]
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

    bt.logging.info(f"Herald forward pass at step {self.step} (consensus {_CONSENSUS_FP})")
    try:
        state = _state(self)
        commit_index, vesting, slash = state.commit_index, state.vesting, state.slash
        block = self.subtensor.get_current_block()
        epoch = max(0, block - HERALD_EPOCH_LAG) // VEST_EPOCH_LEN
        if state.last_scored_epoch >= epoch:
            time.sleep(VALIDATOR_WAIT)  # already scored this epoch; don't re-zero weights
            return

        try:
            now = self.subtensor.get_timestamp(block)  # chain time: validators agree at day boundaries
        except Exception:
            now = None
        briefs = get_briefs(now=now)
        if not briefs:
            bt.logging.info("No active briefs; skipping scoring")
            time.sleep(VALIDATOR_WAIT)
            return
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

        # Reconciliation: merge claims other validators verified + published that this one wasn't
        # served (a miner can serve validators selectively to fork their scores). Hint-only —
        # every merged claim is fully re-verified below.
        results_endpoint = os.getenv("HERALD_RESULTS_ENDPOINT")
        if results_endpoint:
            merged = merge_board_claims(claims_by_uid, fetch_board_results(results_endpoint),
                                        hotkey_by_uid)
            if merged:
                bt.logging.info(f"Reconciled {merged} claim(s) from the board")

        # Require a pinned model when the LLM tier is on, or all validators must agree on the
        # per-provider default (they don't). Without a pin, stay rules-only (deterministic).
        if HERALD_USE_LLM_JUDGE and not HERALD_REF_MODEL_ID:
            bt.logging.warning("HERALD_USE_LLM_JUDGE set without HERALD_REF_MODEL_ID; LLM tier disabled")
        judge_fn = judge if (HERALD_USE_LLM_JUDGE and HERALD_REF_MODEL_ID) else None
        briefs_by_id = {b["id"]: b for b in briefs}

        winners = winning_articles(
            claims_by_uid, commitments, commit_index,
            hotkey_by_uid, alpha_stake_by_uid, briefs, registry,
            fetch_fn=lambda u: fetch_article(u, registry, epoch),
            search_fn=lambda u: in_index(u, epoch),
            judge_fn=judge_fn,
        )
        # Reject claim-organic: the article must be published AFTER the commit appeared. Fail
        # closed — without both the commit block and a publication date we can't prove the article
        # post-dates the commit (vs. pre-existing organic coverage we'd be paying a free-rider for).
        fresh_winners = []
        for w in winners:
            commit_block = commit_index.first_seen_block(w.hotkey, commitments.get(w.hotkey, ""))
            published_ts = fetch_article(w.url, registry, epoch).published_ts
            if commit_block is None or published_ts is None:
                bt.logging.info(f"Rejecting {w.url}: publication date unverifiable vs commit")
                continue
            commit_ts = self.subtensor.get_timestamp(commit_block).timestamp()
            max_ts = commit_ts + HERALD_MAX_PLACEMENT_DAYS * 86400
            if published_ts <= commit_ts or published_ts > max_ts:
                bt.logging.info(f"Rejecting {w.url}: publication date implausible vs commit")
                continue
            fresh_winners.append(w)
        winners = fresh_winners

        for w in winners:
            vesting.start(w.article_id, w.uid, w.usd, w.url, w.hotkey, w.brief_id,
                          w.commit_epoch, epoch)

        # Disputes: register on-chain HRLDDIS flags against active placements. Resolution runs the
        # pinned judge, so it stays OFF unless HERALD_REF_MODEL_ID is set identically on every
        # validator (a mixed fleet would diverge — the same rule as the optional LLM tier).
        disputes = state.disputes
        uid_by_hotkey = {hk: uid for uid, hk in hotkey_by_uid.items()}
        dispute_enabled = bool(HERALD_REF_MODEL_ID)
        dispute_judge_fn = judge if dispute_enabled else None
        if dispute_enabled:
            flags = sorted(  # ascending (block, hotkey): the earliest filer wins one-per-article
                (blk, hk, h) for hk, (val, blk) in commitments_with_block.items()
                if (h := parse_dispute(val)) is not None
            )
            hash_to_article = {article_id_hash(a): a for a in vesting.active_article_ids()}
            for blk, hk, h in flags:
                article_id = hash_to_article.get(h)
                if article_id is None or disputes.is_disputed(article_id):
                    continue
                duid = uid_by_hotkey.get(hk)
                if duid is None:
                    continue  # disputer not a registered UID: can't reward/slash it via weights
                min_bond_alpha = vesting.entry(article_id).total_usd * HERALD_BOND_ALPHA_PER_USD * SLASH_MULTIPLIER
                if alpha_stake_by_uid.get(duid, 0.0) < min_bond_alpha:
                    continue  # under-bonded: ignore (spam/grief deterrent)
                disputes.open(article_id, hk, blk // VEST_EPOCH_LEN)
        elif any(parse_dispute(v) is not None for v in commitments.values()):
            bt.logging.warning("Dispute commits present but HERALD_REF_MODEL_ID unset; disputes disabled")

        # Pass 1: release installments and apply clawbacks/slashes for the whole cycle.
        pending = []
        disputer_rewards = {}  # uid -> usd, funded from forfeited vesting that would otherwise burn
        max_age = vesting.vest_epochs + HERALD_VEST_GRACE_EPOCHS
        for article_id in list(vesting.active_article_ids()):
            entry = vesting.entry(article_id)
            if epoch - entry.start_epoch > max_age:
                disputes.resolve(article_id, upheld=False)  # close any open dispute (inconclusive, no slash)
                vesting.expire(article_id)  # held/incomplete far past its window — terminate
                continue
            disp = disputes.active(article_id)  # None unless an open dispute (and disputes enabled)
            status = _persistence_status(
                entry, briefs_by_id, epoch, dispute_judge_fn if disp is not None else judge_fn,
                registry=registry,
            )
            installment, rewards = settle_persistence(
                article_id, entry, status, epoch,
                vesting=vesting, slash=slash, disputes=disputes,
                dead_confirm=HERALD_DEAD_CONFIRM_EPOCHS, cooldown=SLASH_COOLDOWN_EPOCHS,
                window=HERALD_DISPUTE_WINDOW_EPOCHS, reward_fraction=HERALD_DISPUTE_REWARD_FRACTION,
                uid_by_hotkey=uid_by_hotkey,
            )
            if installment:
                pending.append((entry, installment))
            for duid, amt in rewards.items():
                disputer_rewards[duid] = disputer_rewards.get(duid, 0.0) + amt

        # Pass 2: credit only the original placer, post-slash, and only if it still holds the UID.
        usd_by_uid_brief = {}
        for entry, installment in pending:
            if slash.is_slashed(entry.hotkey, epoch):
                continue
            if hotkey_by_uid.get(entry.uid) != entry.hotkey:
                continue
            key = (entry.uid, entry.brief_id)
            usd_by_uid_brief[key] = usd_by_uid_brief.get(key, 0.0) + installment

        usd_by_uid = apply_reward_pools(usd_by_uid_brief, briefs, HERALD_TOTAL_DAILY_USD, state.pool_spent)
        # Recycle forfeited USD (else burned) to whistleblowers, capped to the budget headroom so a
        # dispute reward only ever consumes what would have burned, never dilutes placement pay.
        payable = {duid: amt for duid, amt in disputer_rewards.items()
                   if not slash.is_slashed(hotkey_by_uid.get(duid, ""), epoch)}
        reward_total = sum(payable.values())
        if reward_total > 0:
            headroom = max(0.0, HERALD_TOTAL_DAILY_USD - sum(usd_by_uid.values()))
            dscale = min(1.0, headroom / reward_total) if HERALD_TOTAL_DAILY_USD > 0 else 1.0
            for duid, amt in payable.items():
                usd_by_uid[duid] = usd_by_uid.get(duid, 0.0) + amt * dscale
        if SUBNET_BURN_UID not in uids:
            bt.logging.warning(f"Burn UID {SUBNET_BURN_UID} not in metagraph; remainder will not burn")
        weights = compute_weights(usd_by_uid, uids, HERALD_TOTAL_DAILY_USD, SUBNET_BURN_UID)
        self.update_scores(weights, uids)
        state.last_scored_epoch = epoch  # only mark scored after success, so a failure retries

        if results_endpoint:
            publish_results(results_endpoint, [{
                "article_id": w.article_id, "hotkey": w.hotkey, "brief_id": w.brief_id,
                "outlet_id": w.outlet_id, "url": w.url, "usd": w.usd,
                "status": vesting.entry(w.article_id).status,
                "attribution": w.level,
                "consensus": _CONSENSUS_FP,
                # vesting coordinates, so a joining validator can bootstrap its ledger
                "commit_epoch": w.commit_epoch,
                "start_epoch": vesting.entry(w.article_id).start_epoch,
                # the full reveal, so other validators can re-verify a claim they weren't served
                # (safe post-claim: the article is public and the commitment already matched)
                "reveal": {
                    "target_outlet_id": getattr(w.claim, "target_outlet_id", w.outlet_id),
                    "nonce": getattr(w.claim, "nonce", ""),
                    "bond_atto": getattr(w.claim, "bond_atto", 0),
                    "version_id": getattr(w.claim, "version_id", 0),
                    "pre_hash": getattr(w.claim, "pre_hash", None),
                    "evidence_text": getattr(w.claim, "evidence_text", None),
                    "evidence_author": getattr(w.claim, "evidence_author", None),
                    "evidence_window": getattr(w.claim, "evidence_window", None),
                    "snapshot_text": getattr(w.claim, "snapshot_text", None),
                },
            } for w in winners])

        path = _state_path(self)
        if path:
            state.save(path)
    except Exception as e:
        bt.logging.error(f"Error in Herald forward pass: {e}")

    time.sleep(VALIDATOR_WAIT)
