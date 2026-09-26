import json
import math
import os
import time

import bittensor as bt
import numpy as np

from herald.validator.utils.briefs import get_briefs
from herald.validator.utils.consensus import consensus_fingerprint
from herald.validator.utils.config import (
    HERALD_DEAD_CONFIRM_EPOCHS,
    HERALD_EPOCH_LAG,
    HERALD_MAX_CANDIDATES_PER_ARTICLE,
    HERALD_MAX_SUBMISSIONS_PER_EPOCH,
    HERALD_REF_MODEL_ID,
    HERALD_USE_LLM_JUDGE,
    HERALD_VEST_GRACE_EPOCHS,
    VALIDATOR_STEPS_INTERVAL,
    VALIDATOR_WAIT,
    VEST_EPOCH_LEN,
)
from .chain import get_commitments_with_block, get_hotkey_owners
from .emission import BURN_UID, apply_reward_pools, miner_weight_vector
from .fetch import fetch_article
from .judge import judge
from .oracle import verify_article
from .pricing import PricingError, daily_miner_usd
from .publish import build_epoch_snapshot, build_result_items, publish_results, publish_snapshot
from .real_news import is_paid
from .registry import load_registry
from .search import in_index
from .state import HeraldState, _json_np_safe
from .submissions import fetch_submissions, select_new, validate_rows
from .url import canonicalize
from .vesting import settle_liveness

# Short hash of every consensus-critical tunable. Validators MUST show the same value; compare
# it across fleet logs (or the `consensus` field on published results) to catch config drift.
_CONSENSUS_FP = consensus_fingerprint()


class _EpochBurn(Exception):
    """An input every article depends on is missing, so the epoch is not scored: all of its weight
    goes to BURN_UID, and the releases it skipped catch up in the next scored epoch."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _persistence_status(entry, briefs_by_id, epoch, judge_fn, registry=None, fetch_fn=None) -> str:
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
    fr = fetch_fn(entry.url) if fetch_fn is not None else fetch_article(entry.url, registry, epoch)
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


def _burn_epoch(self, state: HeraldState, epoch: int, reason: str, *, warn: bool = False):
    """Record `epoch` as scored with all of its weight on BURN_UID, and log why."""
    _set_burn_scores(self)
    state.weight_hotkeys = {}
    state.last_scored_epoch = epoch
    _save_state(self, state)
    (bt.logging.warning if warn else bt.logging.info)(f"EPOCH_BURN epoch={epoch} reason={reason}")


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


def _pass_fetch(registry, epoch):
    """fetch_article for one scoring pass: each canonical URL is fetched at most once.

    Every submission of an article, and the article's liveness check, reads the same page. A fetch
    that raised raises again for its URL without another request.
    """
    fetched = {}

    def fetch_fn(url):
        key = canonicalize(url)
        if key not in fetched:
            try:
                fetched[key] = (fetch_article(url, registry, epoch), None)
            except Exception as exc:
                fetched[key] = (None, exc)
        result, error = fetched[key]
        if error is not None:
            raise error
        return result

    return fetch_fn


def _verify_submission(row, briefs_by_id, registry, fetch_fn, epoch, judge_fn, now_ts):
    """(ArticleResult or None, reason) for one feed row. An error verifying the row rejects only it."""
    brief = briefs_by_id.get(row["brief_id"])
    if brief is None:
        return None, "brief_not_active"
    try:
        result = verify_article(
            row["url"], brief, registry,
            fetch_fn=fetch_fn,
            search_fn=lambda u: in_index(u, epoch),
            judge_fn=judge_fn,
            now_ts=now_ts,
            draft_text=row["draft_text"],
            uploaded_ts=row["uploaded_ts"],
        )
    except Exception as exc:
        bt.logging.warning(f"Verifying submission {row['submission_id']} raised: {exc}")
        return None, "verify_error"
    return result, result.reason


def _state_data(state: HeraldState) -> dict:
    """The ledger exactly as a save writes it, so a failed pass can be discarded."""
    return json.loads(json.dumps(state.to_dict(), default=_json_np_safe))


def _restrict_scored_epoch(self, state: HeraldState):
    """Keep an already scored epoch's scores on UID 0 and the miner UIDs it was scored for.

    No score at all (a restart that lost the vector, or an earlier release's scores that were
    discarded), or a score on a UID the epoch did not score, is replaced by all weight on BURN_UID for
    the rest of the epoch. The ledger is not touched: the next epoch's releases catch up. A scored UID
    that another hotkey has since taken is left to the submission rule, which moves only that UID's
    weight to UID 0 (emission.allowed_emit_vector).
    """
    scores = np.asarray(self.scores, dtype=np.float64)
    support = {int(uid) for uid in np.flatnonzero(np.isfinite(scores) & (scores > 0))}
    if support and support <= {BURN_UID} | set(state.weight_hotkeys):
        return
    _set_burn_scores(self)
    state.weight_hotkeys = {}
    bt.logging.info(f"EPOCH_BURN epoch={state.last_scored_epoch} reason=stale_scores")


def _burn_reason(exc: Exception) -> str:
    if isinstance(exc, _EpochBurn):
        return exc.reason
    if isinstance(exc, PricingError):
        return f"pricing_error ({exc})"
    return f"error ({type(exc).__name__}: {exc})"


def _fail_epoch(self, epoch: int, before: dict, reason: str):
    """The epoch's scoring failed: every ledger change this pass made is discarded and all of the
    epoch's weight goes to BURN_UID.

    Installments that were released or drawn from a pool during the failed pass are released again
    by the next successful epoch, because a release catches up on missed epochs.
    """
    try:
        state = HeraldState.from_dict(before)
        self.herald_state = state
        _burn_epoch(self, state, epoch, reason, warn=True)
    except Exception as e:
        bt.logging.error(f"Saving the weights for epoch {epoch} failed: {e}")


def _resync_before_scoring(self):
    try:
        self.resync_metagraph()
    except Exception as exc:
        raise _EpochBurn("metagraph_unavailable") from exc


def _miner_uids(self) -> dict:
    """{hotkey: uid} for every hotkey registered on the subnet except the owner's UID 0."""
    return {hotkey: uid for uid, hotkey in enumerate(self.metagraph.hotkeys) if uid != BURN_UID}


def _score_epoch(self, state: HeraldState, epoch: int, block: int):
    vesting = state.vesting
    network = getattr(self.subtensor, "network", None) or getattr(
        getattr(self.config, "subtensor", None), "network", "unknown"
    )
    try:
        now = self.subtensor.get_timestamp(block)  # chain time: validators agree at day boundaries
    except Exception:
        now = None
    briefs = get_briefs(now=now)
    if not briefs:
        # An explicit, successfully verified empty feed means there is no authorized work to pay.
        _burn_epoch(self, state, epoch, "no_briefs")
        return

    now_ts = now.timestamp() if now is not None else 0.0
    if now_ts <= 0:
        raise _EpochBurn("chain_time_unavailable")
    # Price the day before fetching anything: without a price nothing can be paid.
    price = daily_miner_usd(self.subtensor, self.config.netuid, block)

    registry = _load_registry(self, block, network)
    results_endpoint = os.getenv("HERALD_RESULTS_ENDPOINT")
    rows = fetch_submissions(results_endpoint, network, self.config.netuid)
    if rows is None:
        raise _EpochBurn("feed_unavailable")
    judge_fn = _judge_fn(briefs)
    briefs_by_id = {b["id"]: b for b in briefs}
    uid_of = _miner_uids(self)

    # New submissions: each verified article starts one vesting entry on the hotkey its contributor
    # signed for. Articles are taken earliest upload first; within an article the candidates are
    # tried earliest upload first, and the first that passes every check is credited.
    valid_rows = validate_rows(rows, network, self.config.netuid, now_ts)
    selected = select_new(valid_rows, vesting, HERALD_MAX_SUBMISSIONS_PER_EPOCH,
                          HERALD_MAX_CANDIDATES_PER_ARTICLE)
    bt.logging.info(
        f"Submissions feed: {len(rows)} row(s), {len(valid_rows)} valid, {len(selected)} to verify"
    )
    # Whether each signing coldkey owns its hotkey is read from the chain at the scoring block.
    owners = get_hotkey_owners(self.subtensor, [row["hotkey"] for _aid, candidates in selected
                                                for row in candidates], block=block)
    fetch_fn = _pass_fetch(registry, epoch)
    for aid, candidates in selected:
        for position, row in enumerate(candidates, start=1):
            if owners.get(row["hotkey"]) != row["coldkey"]:
                bt.logging.info(f"SUBMISSION_RESULT {row['submission_id']} hotkey_not_owned")
                continue
            result, reason = _verify_submission(row, briefs_by_id, registry, fetch_fn, epoch, judge_fn,
                                                now_ts)
            bt.logging.info(f"SUBMISSION_RESULT {row['submission_id']} {reason}")
            if result is None or not result.passed:
                continue
            outlet = registry.lookup(row["url"])
            vesting.start(
                aid, uid_of.get(row["hotkey"], -1), result.usd, row["url"],
                row["hotkey"], row["brief_id"], commit_epoch=epoch, start_epoch=epoch,
                outlet_id=outlet.outlet_id, tier=outlet.tier, attribution=0,
                reveal={"submission_id": row["submission_id"], "coldkey": row["coldkey"]},
            )
            bt.logging.info(
                f"SUBMISSION_CREDITED {row['submission_id']} candidate={position}/{len(candidates)} "
                f"hotkey={row['hotkey']}"
            )
            break

    # Pass 1: expiry, then liveness for every vesting article. There is no slashing. An article whose
    # hotkey is not registered holds: nothing is released until the hotkey registers again, and the
    # releases it missed then catch up, up to the article's maximum age.
    pending = []
    legacy_expired = 0
    held = 0
    max_age = vesting.vest_epochs + HERALD_VEST_GRACE_EPOCHS
    for aid in list(vesting.active_article_ids()):
        entry = vesting.entry(aid)
        if epoch - entry.start_epoch > max_age:
            vesting.expire(aid)  # held/incomplete far past its window — terminate
            continue
        reveal = entry.reveal if isinstance(entry.reveal, dict) else {}
        if not (reveal.get("submission_id") and reveal.get("coldkey")):
            # Only entries started from a signed feed submission vest: earlier releases' entries,
            # which vested on one shared incentive hotkey, expire.
            if vesting.expire(aid):
                legacy_expired += 1
            continue
        try:
            status = _persistence_status(entry, briefs_by_id, epoch, judge_fn, registry=registry,
                                         fetch_fn=fetch_fn)
        except Exception as exc:
            bt.logging.warning(f"Liveness check for {entry.url} raised: {exc}; holding")
            status = "hold"
        entry.uid = uid_of.get(entry.hotkey, -1)
        if status == "alive" and entry.uid < 0:
            status = "hold"
            held += 1
        installment = settle_liveness(
            aid, entry, status, epoch, vesting=vesting, dead_confirm=HERALD_DEAD_CONFIRM_EPOCHS,
        )
        if installment:
            pending.append((entry, installment))
    if legacy_expired:
        bt.logging.info(f"LEGACY_VESTING_EXPIRED {legacy_expired}")
    if held:
        bt.logging.info(f"VESTING_HELD_UNREGISTERED {held}")

    # Pass 2: client reward pools cap each brief's installments; the rest is paid per miner UID.
    values = {}
    for entry, installment in pending:
        values.setdefault((entry.uid, entry.brief_id), []).append(installment)
    installments = {key: math.fsum(sorted(amounts)) for key, amounts in values.items()}
    usd_by_uid = apply_reward_pools(installments, briefs, state.pool_spent)
    payable_usd = math.fsum(usd_by_uid[uid] for uid in sorted(usd_by_uid))

    # Each miner UID's weight is its payable USD over the USD value of the day's miner emission, and
    # UID 0 receives the rest, which is burned.
    uids, weights = miner_weight_vector(usd_by_uid, price["daily_usd"])
    hotkeys = list(self.metagraph.hotkeys)
    self.scores[...] = 0
    self.update_scores(weights, uids)
    state.last_scored_epoch = epoch
    state.weight_hotkeys = {int(uid): hotkeys[uid] for uid in uids if uid != BURN_UID}
    weight_by_uid = {int(uid): float(weight) for uid, weight in zip(uids, weights)}

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
        vesting, briefs, state.pool_spent, usd_by_uid,
        [float(weight) for weight in weights], uids,
        {uid: hotkeys[uid] for uid in set(uids) | set(usd_by_uid)},
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
    bt.logging.info(
        f"EPOCH_WEIGHTS epoch={epoch} miners={len(usd_by_uid)} payable_usd={payable_usd:.6f} "
        f"daily_usd={price['daily_usd']:.6f} burn={weight_by_uid.get(BURN_UID, 0.0):.6f} "
        f"alpha_tao={price['alpha_tao']:.9f} tao_usd={price['tao_usd']:.6f} "
        f"daily_miner_alpha={price['daily_miner_alpha']:.6f}"
    )
    for uid in sorted(usd_by_uid):
        bt.logging.info(
            f"MINER_WEIGHT epoch={epoch} uid={uid} hotkey={hotkeys[uid]} "
            f"usd={usd_by_uid[uid]:.6f} w={weight_by_uid.get(uid, 0.0):.6f}"
        )


async def forward(self):
    if self.step % VALIDATOR_STEPS_INTERVAL != 0:
        time.sleep(VALIDATOR_WAIT)
        return

    bt.logging.info(f"Herald forward pass at step {self.step} (consensus {_CONSENSUS_FP})")
    try:
        state = _state(self)
        block = self.subtensor.get_current_block()
        epoch = max(0, block - HERALD_EPOCH_LAG) // VEST_EPOCH_LEN
        scored = state.last_scored_epoch >= epoch
        before = None if scored else _state_data(state)
    except Exception as e:
        # No epoch is known without the ledger and the chain block: nothing is scored or burned.
        bt.logging.error(f"Error in Herald forward pass: {e}")
        time.sleep(VALIDATOR_WAIT)
        return

    if scored:
        try:
            _restrict_scored_epoch(self, state)
        except Exception as e:
            bt.logging.error(f"Checking the scored epoch's weights failed: {e}")
        time.sleep(VALIDATOR_WAIT)  # already scored this epoch; don't re-score it
        return

    try:
        # Registrations are read as they stand when the epoch is scored, not at the last periodic
        # sync: a hotkey that lost its UID since then holds, instead of being released to a UID that
        # has changed hands (and burned there). A failed resync burns the epoch; nothing is lost, the
        # releases catch up with the next one.
        _resync_before_scoring(self)
        _score_epoch(self, state, epoch, block)
    except Exception as e:
        _fail_epoch(self, epoch, before, _burn_reason(e))
    time.sleep(VALIDATOR_WAIT)
