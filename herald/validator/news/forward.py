import math
import os
import time

import bittensor as bt

from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.consensus import consensus_fingerprint
from herald.validator.utils.config import (
    HERALD_DEAD_CONFIRM_EPOCHS,
    HERALD_EPOCH_LAG,
    HERALD_INCENTIVE_HOTKEY,
    HERALD_MAX_SUBMISSIONS_PER_EPOCH,
    HERALD_REF_MODEL_ID,
    HERALD_USE_LLM_JUDGE,
    HERALD_VEST_GRACE_EPOCHS,
    VALIDATOR_STEPS_INTERVAL,
    VALIDATOR_WAIT,
    VEST_EPOCH_LEN,
)
from .chain import get_commitments_with_block
from .emission import apply_reward_pools
from .fetch import fetch_article
from .judge import judge
from .oracle import verify_article
from .publish import build_epoch_snapshot, build_result_items, publish_results, publish_snapshot
from .real_news import is_paid
from .registry import load_registry
from .search import in_index
from .state import HeraldState
from .submissions import fetch_submissions, select_new, validate_rows
from .vesting import settle_liveness

# Short hash of every consensus-critical tunable. Validators MUST show the same value; compare
# it across fleet logs (or the `consensus` field on published results) to catch config drift.
_CONSENSUS_FP = consensus_fingerprint()

# The UID that receives the epoch's weight vector.
BURN_UID = 0


def _persistence_status(entry, briefs_by_id, epoch, judge_fn, registry=None) -> str:
    """alive (release), dead (counts toward clawback), or hold (unconfirmed: change nothing).

    Gates the per-epoch installment on LIVENESS ONLY: a reachable, non-thin page that hasn't
    turned into an ad. Topic match and search-index presence were already verified when the article
    was accepted; re-checking them here on each validator's own live fetch only forks per-epoch pay
    across the fleet, because search results and page variants legitimately differ per validator.
    A dead verdict needs a CONFIRMED removal (404/410) or a confirmed change to paid content; a
    transient failure holds.
    """
    if entry.brief_id not in briefs_by_id:
        return "hold"  # brief closed/defunded: withhold its installment
    fr = fetch_article(entry.url, registry, epoch)
    if fr.status in (404, 410):
        return "dead"
    if not fr.ok:
        return "hold"  # status 0/451/5xx or thin body — geo-block/no-connect: can't confirm live
    outlet = registry.lookup(entry.url) if registry is not None else None
    paid_text = getattr(fr, "article_text", None) or fr.text
    if is_paid(entry.url, paid_text, judge_fn, outlet=outlet)[0]:
        return "dead"  # changed to paid/sponsored content
    return "alive"


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


def _save_state(self, state: HeraldState):
    path = _state_path(self)
    if path:
        state.save(path)


def _set_burn_scores(self):
    """Replace the score vector with all weight on BURN_UID."""
    self.scores[...] = 0
    self.update_scores([1.0], [BURN_UID])


def _load_registry(self, block: int, network):
    """The outlet registry, verified against the registry authority's on-chain anchor when an
    authority hotkey is configured. That anchor is the only commitment this validator reads."""
    authority = os.getenv("HERALD_REGISTRY_AUTHORITY_HOTKEY")
    if not authority:
        return load_registry()
    commitments = get_commitments_with_block(self.subtensor, self.config.netuid)
    anchor_value, _set_block = commitments.get(authority, (None, None))
    return load_registry(anchor_value, require_anchor=True, current_block=block,
                         network=str(network), netuid=self.config.netuid)


def _incentive_uid(self, hotkey: str):
    hotkeys = list(self.metagraph.hotkeys)
    return hotkeys.index(hotkey) if hotkey in hotkeys else None


def _judge_fn(briefs):
    # Require a pinned model when the LLM tier is on, or all validators must agree on the
    # per-provider default (they don't). Without a pin, stay rules-only (deterministic).
    if HERALD_USE_LLM_JUDGE and not HERALD_REF_MODEL_ID:
        bt.logging.warning("HERALD_USE_LLM_JUDGE set without HERALD_REF_MODEL_ID; LLM tier disabled")
    judge_fn = judge if (HERALD_USE_LLM_JUDGE and HERALD_REF_MODEL_ID) else None
    # topic_matched() falls through to `not keywords`, so a brief with no keywords and no LLM
    # judge accepts ANY article as on-topic. Warn loudly so an operator sees it in the logs.
    if judge_fn is None:
        ungated = [b.get("id") for b in briefs if not (b.get("keywords") or [])]
        if ungated:
            bt.logging.warning(
                "No topic gate for brief(s) %s: they carry no keywords and the LLM judge is "
                "off, so every article passes the topic check. Set keywords on the brief, or "
                "enable HERALD_USE_LLM_JUDGE with a pinned HERALD_REF_MODEL_ID fleet-wide."
                % ", ".join(str(i) for i in ungated)
            )
    return judge_fn


def _verify_submission(row, briefs_by_id, registry, epoch, judge_fn, now_ts):
    """(ArticleResult or None, reason) for one feed row. An error verifying the row rejects only it."""
    brief = briefs_by_id.get(row["brief_id"])
    if brief is None:
        return None, "brief_not_active"
    try:
        result = verify_article(
            row["url"], brief, registry,
            fetch_fn=lambda u: fetch_article(u, registry, epoch),
            search_fn=lambda u: in_index(u, epoch),
            judge_fn=judge_fn,
            now_ts=now_ts,
        )
    except Exception as exc:
        bt.logging.warning(f"Verifying submission {row['submission_id']} raised: {exc}")
        return None, "verify_error"
    return result, result.reason


async def forward(self):
    if self.step % VALIDATOR_STEPS_INTERVAL != 0:
        time.sleep(VALIDATOR_WAIT)
        return

    bt.logging.info(f"Herald forward pass at step {self.step} (consensus {_CONSENSUS_FP})")
    try:
        state = _state(self)
        vesting = state.vesting
        block = self.subtensor.get_current_block()
        network = getattr(self.subtensor, "network", None) or getattr(
            getattr(self.config, "subtensor", None), "network", "unknown"
        )
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
            # An explicit, successfully verified empty feed means there is no authorized work to pay.
            _set_burn_scores(self)
            state.last_scored_epoch = epoch
            _save_state(self, state)
            bt.logging.info(f"No active briefs; epoch {epoch} weight goes to UID {BURN_UID}")
            time.sleep(VALIDATOR_WAIT)
            return

        incentive_hotkey = HERALD_INCENTIVE_HOTKEY
        if not incentive_hotkey:
            raise RuntimeError("HERALD_INCENTIVE_HOTKEY is not set")
        now_ts = now.timestamp() if now is not None else 0.0
        if now_ts <= 0:
            raise RuntimeError(f"chain time for block {block} is unavailable")

        registry = _load_registry(self, block, network)
        results_endpoint = os.getenv("HERALD_RESULTS_ENDPOINT")
        rows = fetch_submissions(results_endpoint, network, self.config.netuid)
        if rows is None:
            raise RuntimeError("submissions feed unavailable")  # nothing scored: the epoch retries
        uid_star = _incentive_uid(self, incentive_hotkey)
        judge_fn = _judge_fn(briefs)
        briefs_by_id = {b["id"]: b for b in briefs}

        # New submissions: each verified article starts one vesting entry on the incentive hotkey.
        valid_rows = validate_rows(rows, network, self.config.netuid)
        selected = select_new(valid_rows, vesting, HERALD_MAX_SUBMISSIONS_PER_EPOCH)
        bt.logging.info(
            f"Submissions feed: {len(rows)} row(s), {len(valid_rows)} valid, {len(selected)} to verify"
        )
        for aid, row in selected:
            result, reason = _verify_submission(row, briefs_by_id, registry, epoch, judge_fn, now_ts)
            bt.logging.info(f"SUBMISSION_RESULT {row['submission_id']} {reason}")
            if result is None or not result.passed:
                continue
            outlet = registry.lookup(row["url"])
            vesting.start(
                aid, uid_star if uid_star is not None else -1, result.usd, row["url"],
                incentive_hotkey, row["brief_id"], commit_epoch=epoch, start_epoch=epoch,
                outlet_id=outlet.outlet_id, tier=outlet.tier, attribution=0,
                reveal={"submission_id": row["submission_id"]},
            )

        # Pass 1: expiry, then liveness for every vesting article. There is no slashing.
        pending = []
        legacy_expired = 0
        max_age = vesting.vest_epochs + HERALD_VEST_GRACE_EPOCHS
        for aid in list(vesting.active_article_ids()):
            entry = vesting.entry(aid)
            if epoch - entry.start_epoch > max_age:
                vesting.expire(aid)  # held/incomplete far past its window — terminate
                continue
            reveal = entry.reveal if isinstance(entry.reveal, dict) else {}
            if not reveal.get("submission_id"):
                # Only entries started from a feed submission vest.
                if vesting.expire(aid):
                    legacy_expired += 1
                continue
            try:
                status = _persistence_status(entry, briefs_by_id, epoch, judge_fn, registry=registry)
            except Exception as exc:
                bt.logging.warning(f"Liveness check for {entry.url} raised: {exc}; holding")
                status = "hold"
            installment = settle_liveness(
                aid, entry, status, epoch, vesting=vesting, dead_confirm=HERALD_DEAD_CONFIRM_EPOCHS,
            )
            if installment:
                pending.append((entry, installment))
        if legacy_expired:
            bt.logging.info(f"LEGACY_VESTING_EXPIRED {legacy_expired}")

        # Pass 2: client reward pools cap each brief's installments.
        values_by_brief = {}
        for entry, installment in pending:
            values_by_brief.setdefault((-1, entry.brief_id), []).append(installment)
        installments = {key: math.fsum(sorted(values)) for key, values in values_by_brief.items()}
        paid = apply_reward_pools(installments, briefs, state.pool_spent)
        payable_usd = math.fsum(sorted(paid.values()))

        # Each evaluation epoch is a complete daily allocation: the whole vector goes to BURN_UID.
        _set_burn_scores(self)
        state.last_scored_epoch = epoch  # only mark scored after success, so a failure retries
        bt.logging.info(f"Epoch {epoch}: payable_usd={payable_usd:.6f}; weight goes to UID {BURN_UID}")

        publish_results(results_endpoint, build_result_items(
            vesting,
            network=network,
            netuid=self.config.netuid,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            validator_uid=self.uid,
            chain_block=block,
            registry_version=registry.version_id,
            consensus=_CONSENSUS_FP,
        ))
        publish_snapshot(results_endpoint, build_epoch_snapshot(
            vesting, briefs, state.pool_spent, {}, [1.0], [BURN_UID],
            {BURN_UID: self.metagraph.hotkeys[BURN_UID]},
            network=network,
            netuid=self.config.netuid,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            validator_uid=self.uid,
            chain_block=block,
            epoch=epoch,
            registry_version=registry.version_id,
            registry_hash=registry.content_hash,
            consensus=_CONSENSUS_FP,
        ), self.wallet.hotkey)

        _save_state(self, state)
    except Exception as e:
        bt.logging.error(f"Error in Herald forward pass: {e}")

    time.sleep(VALIDATOR_WAIT)
