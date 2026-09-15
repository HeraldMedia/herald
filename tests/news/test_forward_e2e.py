from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from herald.validator.news import forward as fwd
from herald.validator.news import state as statemod
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.state import HeraldState
from herald.validator.news.url import article_id

STAR = "hkStar"
HOTKEYS = ["hkOwner", "hkMiner", STAR]
UID_STAR = 2
AUTHORITY = "hkAuthority"
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc).timestamp()
REGISTRY = OutletRegistry.from_dict({"version_id": 3, "outlets": [
    {"outlet_id": "guardian", "tier": 1, "domains": ["www.theguardian.com"]},
    {"outlet_id": "techcrunch", "tier": 2, "domains": ["techcrunch.com"]},
]})
URL_A = "https://www.theguardian.com/world/2026/sep/10/subnet-pilot"
URL_B = "https://techcrunch.com/2026/09/10/subnet-pilot"
STANDING = [{"id": "b1", "kind": "standing"}]
BODY = "A news report about the subnet pilot."


def row(submission_id, url, brief_id="b1"):
    return {"submission_id": submission_id, "network": "finney", "netuid": 69,
            "brief_id": brief_id, "url": url}


def live_page(url):
    return SimpleNamespace(ok=True, status=200, final_url=url, text_hash="h", text=BODY,
                           article_text=None, published_ts=PUBLISHED)


def missing_page(url):
    return SimpleNamespace(ok=False, status=404, final_url=url, text_hash="", text="",
                           article_text=None, published_ts=None)


@pytest.fixture
def env(monkeypatch):
    env = SimpleNamespace(
        briefs=STANDING, rows=[], pages={}, block=fwd.VEST_EPOCH_LEN * 1000 + 100,
        feed_calls=0, commitment_reads=0, registry_calls=[], updates=[], results=[],
        snapshots=[], logs=[],
    )
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 2)
    monkeypatch.setattr(fwd, "HERALD_INCENTIVE_HOTKEY", STAR)
    monkeypatch.setattr(fwd, "HERALD_DEAD_CONFIRM_EPOCHS", 2)
    monkeypatch.setattr(fwd.time, "sleep", lambda *_: None)
    monkeypatch.setenv("HERALD_RESULTS_ENDPOINT", "http://results.invalid")
    monkeypatch.setattr(fwd, "get_briefs", lambda now=None: env.briefs)

    def feed(endpoint, network, netuid):
        env.feed_calls += 1
        return None if env.rows is None else list(env.rows)

    def commitments(subtensor, netuid):
        env.commitment_reads += 1
        return {AUTHORITY: ("HRLDREG|anchor", 5), "hkMiner": ("HRLD1|other", 6)}

    def registry(*args, **kwargs):
        env.registry_calls.append((args, kwargs))
        return REGISTRY

    monkeypatch.setattr(fwd, "fetch_submissions", feed)
    monkeypatch.setattr(fwd, "get_commitments_with_block", commitments)
    monkeypatch.setattr(fwd, "load_registry", registry)
    monkeypatch.setattr(fwd, "fetch_article",
                        lambda url, registry=None, epoch=None: env.pages.get(url, live_page)(url))
    monkeypatch.setattr(fwd, "in_index",
                        lambda url, epoch=None: SimpleNamespace(in_index=True, matched_url=url))
    monkeypatch.setattr(fwd, "publish_results", lambda endpoint, rows: env.results.append(rows))
    monkeypatch.setattr(fwd, "publish_snapshot",
                        lambda endpoint, snapshot, hotkey: env.snapshots.append(snapshot) or True)
    for level in ("info", "warning", "error"):
        monkeypatch.setattr(fwd.bt.logging, level, lambda msg, *a, **k: env.logs.append(str(msg)))
    return env


def make_validator(env, hotkeys=HOTKEYS, scores=None):
    """A validator with only what scoring may use: no axons, no dendrite, no stake."""
    self = SimpleNamespace(
        step=0,
        uid=0,
        config=SimpleNamespace(netuid=69, neuron=SimpleNamespace(moving_average_alpha=1.0)),
        subtensor=SimpleNamespace(network="finney", get_current_block=lambda: env.block,
                                  get_timestamp=lambda block: NOW),
        metagraph=SimpleNamespace(hotkeys=list(hotkeys)),
        wallet=SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkValidator")),
        scores=np.zeros(len(hotkeys), dtype=np.float32) if scores is None
        else np.asarray(scores, dtype=np.float32),
    )

    def update_scores(rewards, uids):
        scattered = np.zeros_like(self.scores)
        scattered[np.asarray(uids)] = rewards
        alpha = self.config.neuron.moving_average_alpha
        self.scores = alpha * scattered + (1 - alpha) * self.scores
        env.updates.append((list(uids), [float(r) for r in rewards]))

    self.update_scores = update_scores
    return self


def epoch_of(env):
    return (env.block - fwd.HERALD_EPOCH_LAG) // fwd.VEST_EPOCH_LEN


def next_epoch(env):
    env.block += fwd.VEST_EPOCH_LEN


def results_for(env, reason):
    return [line for line in env.logs if line.startswith("SUBMISSION_RESULT") and line.endswith(" " + reason)]


@pytest.mark.asyncio
async def test_verified_submission_vests_on_the_incentive_hotkey_with_its_first_installment(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.1, 0.6, 0.3])

    await fwd.forward(self)

    epoch = epoch_of(env)
    state = self.herald_state
    assert state.last_scored_epoch == epoch
    entry = state.vesting.entry(article_id(URL_A))
    assert (entry.hotkey, entry.uid, entry.reveal) == (STAR, UID_STAR, {"submission_id": "sub-1"})
    assert (entry.url, entry.brief_id, entry.outlet_id, entry.tier, entry.attribution) == (
        URL_A, "b1", "guardian", 1, 0)
    assert (entry.commit_epoch, entry.start_epoch, entry.last_release_epoch) == (epoch, epoch, epoch)
    assert entry.total_usd == pytest.approx(500.0) and entry.remaining == 1
    assert "SUBMISSION_RESULT sub-1 ok" in env.logs
    assert env.updates == [([0], [1.0])]
    assert self.scores.tolist() == [1.0, 0.0, 0.0]
    assert env.commitment_reads == 0

    [snapshot] = env.snapshots
    assert snapshot["epoch"] == epoch and snapshot["state"]["rewards"] == []
    assert snapshot["state"]["weights"] == [{"uid": 0, "hotkey": "hkOwner", "weight_u16": 65535}]
    [article] = snapshot["state"]["articles"]
    assert (article["hotkey"], article["reveal"]) == (STAR, {"submission_id": "sub-1"})
    assert article["earned_microusd"] == 250_000_000
    [[published]] = env.results
    assert published["reveal"] == {"submission_id": "sub-1"}


@pytest.mark.asyncio
async def test_the_same_article_later_does_not_start_a_second_entry(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)

    next_epoch(env)
    env.rows = [row("sub-0", URL_A + "?utm_source=feed"), row("sub-7", URL_A)]
    await fwd.forward(self)

    entries = self.herald_state.vesting.to_dict()["entries"]
    assert list(entries) == [article_id(URL_A)]
    assert entries[article_id(URL_A)]["reveal"] == {"submission_id": "sub-1"}
    assert entries[article_id(URL_A)]["status"] == "COMPLETED"
    assert not [line for line in env.logs if line.startswith(("SUBMISSION_RESULT sub-0", "SUBMISSION_RESULT sub-7"))]


@pytest.mark.asyncio
async def test_a_row_that_raises_during_verification_is_rejected_alone(env):
    def broken(url):
        raise RuntimeError("page parser failed")

    env.pages[URL_A] = broken
    env.rows = [row("sub-1", URL_A), row("sub-2", URL_B)]
    self = make_validator(env)

    await fwd.forward(self)

    assert "SUBMISSION_RESULT sub-1 verify_error" in env.logs
    assert "SUBMISSION_RESULT sub-2 ok" in env.logs
    vesting = self.herald_state.vesting
    assert not vesting.has(article_id(URL_A))
    assert vesting.entry(article_id(URL_B)).total_usd == pytest.approx(300.0)
    assert self.herald_state.last_scored_epoch == epoch_of(env)


@pytest.mark.asyncio
async def test_a_liveness_check_that_raises_holds_the_article(env, monkeypatch):
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 4)
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    first_epoch = epoch_of(env)
    entry = self.herald_state.vesting.entry(article_id(URL_A))

    def broken(*args, **kwargs):
        raise RuntimeError("liveness check failed")

    real_status = fwd._persistence_status
    monkeypatch.setattr(fwd, "_persistence_status", broken)
    next_epoch(env)
    await fwd.forward(self)
    assert self.herald_state.last_scored_epoch == epoch_of(env)
    assert (entry.status, entry.remaining, entry.last_release_epoch) == ("VESTING", 3, first_epoch)
    assert (entry.dead_streak, entry.last_dead_epoch) == (0, -1)

    monkeypatch.setattr(fwd, "_persistence_status", real_status)
    next_epoch(env)
    await fwd.forward(self)
    assert (entry.remaining, entry.last_release_epoch) == (1, epoch_of(env))  # the held epoch catches up


@pytest.mark.asyncio
async def test_entries_without_a_submission_id_expire_once(env):
    self = make_validator(env)
    epoch = epoch_of(env)
    state = HeraldState.fresh()
    state.vesting.start("legacy", uid=1, total_usd=50.0, url=URL_B, hotkey="hkMiner", brief_id="b1",
                        commit_epoch=epoch - 1, start_epoch=epoch, reveal={"nonce": "n1"})
    state.vesting.start("other-hotkey", uid=1, total_usd=500.0, url=URL_A, hotkey="hkEarlierIncentive",
                        brief_id="b1", commit_epoch=epoch, start_epoch=epoch,
                        reveal={"submission_id": "sub-0"})
    self.herald_state = state

    await fwd.forward(self)
    assert state.vesting.status("legacy") == "EXPIRED"
    other = state.vesting.entry("other-hotkey")
    assert other.status == "VESTING" and other.last_release_epoch == epoch
    assert env.logs.count("LEGACY_VESTING_EXPIRED 1") == 1

    next_epoch(env)
    await fwd.forward(self)
    assert state.vesting.status("legacy") == "EXPIRED"
    assert state.vesting.status("other-hotkey") == "COMPLETED"
    assert len([line for line in env.logs if line.startswith("LEGACY_VESTING_EXPIRED")]) == 1


@pytest.mark.asyncio
async def test_article_dead_for_two_epochs_is_clawed_back_without_a_slash(env, monkeypatch):
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 4)
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    entry = self.herald_state.vesting.entry(article_id(URL_A))

    env.pages[URL_A] = missing_page
    next_epoch(env)
    await fwd.forward(self)
    assert (entry.status, entry.dead_streak) == ("VESTING", 1)

    next_epoch(env)
    await fwd.forward(self)
    assert (entry.status, entry.dead_streak, entry.remaining) == ("CLAWBACK", 2, 3)
    assert self.herald_state.slash.to_dict() == {"until": {}}
    assert self.herald_state.disputes.to_dict() == {}
    assert self.herald_state.last_scored_epoch == epoch_of(env)


@pytest.mark.asyncio
async def test_client_reward_pool_caps_installments(env):
    env.briefs = [{"id": "b1", "kind": "client", "reward_pool": 100.0,
                   "start_date": "2026-09-01", "end_date": "2026-09-30"}]
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)

    await fwd.forward(self)
    assert self.herald_state.pool_spent == {"b1": pytest.approx(100.0)}
    assert any(line.endswith("payable_usd=100.000000; weight goes to UID 0") for line in env.logs)
    [brief_row] = env.snapshots[0]["state"]["briefs"]
    assert (brief_row["pool_spent_microusd"], brief_row["pool_remaining_microusd"]) == (100_000_000, 0)

    next_epoch(env)
    await fwd.forward(self)
    assert self.herald_state.pool_spent == {"b1": pytest.approx(100.0)}
    assert any(line.endswith("payable_usd=0.000000; weight goes to UID 0") for line in env.logs)
    assert self.herald_state.vesting.status(article_id(URL_A)) == "COMPLETED"


@pytest.mark.asyncio
async def test_empty_briefs_put_all_weight_on_uid_zero_without_reading_chain_commitments(env, monkeypatch):
    monkeypatch.setenv("HERALD_REGISTRY_AUTHORITY_HOTKEY", AUTHORITY)
    env.briefs = []
    self = make_validator(env, scores=[0.2, 0.5, 0.3])

    await fwd.forward(self)

    assert env.updates == [([0], [1.0])]
    assert self.scores.tolist() == [1.0, 0.0, 0.0]
    assert self.herald_state.last_scored_epoch == epoch_of(env)
    assert (env.commitment_reads, env.feed_calls, env.registry_calls) == (0, 0, [])
    assert env.snapshots == [] and env.results == []


@pytest.mark.asyncio
async def test_forward_never_uses_axons_or_the_dendrite(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    assert not hasattr(self, "dendrite") and not hasattr(self.metagraph, "axons")

    await fwd.forward(self)

    assert self.herald_state.last_scored_epoch == epoch_of(env)
    assert self.herald_state.vesting.has(article_id(URL_A))


@pytest.mark.asyncio
async def test_same_epoch_rerun_is_skipped(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    await fwd.forward(self)
    assert len(env.updates) == 1 and env.feed_calls == 1


@pytest.mark.asyncio
async def test_unreadable_feed_leaves_the_epoch_unscored_and_retries(env):
    env.rows = None
    self = make_validator(env)
    await fwd.forward(self)
    assert self.herald_state.last_scored_epoch == -1 and env.updates == []

    env.rows = [row("sub-1", URL_A)]
    await fwd.forward(self)
    assert self.herald_state.last_scored_epoch == epoch_of(env)
    assert self.herald_state.vesting.has(article_id(URL_A))


@pytest.mark.asyncio
async def test_unset_incentive_hotkey_leaves_the_epoch_unscored(env, monkeypatch):
    monkeypatch.setattr(fwd, "HERALD_INCENTIVE_HOTKEY", "")
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    assert self.herald_state.last_scored_epoch == -1
    assert env.feed_calls == 0 and env.updates == []


@pytest.mark.asyncio
async def test_registry_anchor_is_the_only_commitment_read(env, monkeypatch):
    monkeypatch.setenv("HERALD_REGISTRY_AUTHORITY_HOTKEY", AUTHORITY)
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)

    await fwd.forward(self)

    assert env.commitment_reads == 1
    [(args, kwargs)] = env.registry_calls
    assert args == ("HRLDREG|anchor",)
    assert kwargs["require_anchor"] is True and kwargs["netuid"] == 69
    assert self.herald_state.last_scored_epoch == epoch_of(env)


@pytest.mark.asyncio
async def test_row_for_a_brief_not_on_the_board_does_not_vest(env):
    env.rows = [row("sub-1", URL_A, brief_id="closed-brief")]
    self = make_validator(env)
    await fwd.forward(self)
    assert results_for(env, "brief_not_active") == ["SUBMISSION_RESULT sub-1 brief_not_active"]
    assert not self.herald_state.vesting.has(article_id(URL_A))


@pytest.mark.asyncio
async def test_rejected_article_logs_its_reason_and_does_not_vest(env):
    env.pages[URL_A] = missing_page
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    assert "SUBMISSION_RESULT sub-1 url_not_live" in env.logs
    assert self.herald_state.vesting.to_dict()["entries"] == {}


@pytest.mark.asyncio
async def test_incentive_hotkey_without_a_uid_vests_with_uid_minus_one(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, hotkeys=["hkOwner", "hkMiner"])
    await fwd.forward(self)
    entry = self.herald_state.vesting.entry(article_id(URL_A))
    assert (entry.uid, entry.hotkey) == (-1, STAR)


def test_persistence_holds_when_brief_left_the_board(monkeypatch):
    # A live article whose brief is no longer on the signed board holds: the closed brief has no
    # reward_pool/kind for apply_reward_pools to draw from.
    monkeypatch.setattr(fwd, "fetch_article", lambda url, registry=None, epoch=None: live_page(url))
    entry = SimpleNamespace(url=URL_A, brief_id="gone")
    assert fwd._persistence_status(entry, {"b1": {"id": "b1"}}, epoch=1, judge_fn=None) == "hold"


def test_persistence_pays_live_article_despite_offtopic_or_deindexed_fetch(monkeypatch):
    # The per-epoch pay gate is liveness only. Topic and search presence were verified when the
    # article was accepted; a live, non-paid page stays "alive" even when this validator's fetch
    # looks off-topic and its search index does not list the URL.
    entry = SimpleNamespace(url=URL_A, brief_id="b1")
    briefs_by_id = {"b1": {"id": "b1", "keywords": ["bittensor"]}}
    monkeypatch.setattr(fwd, "fetch_article", lambda url, registry=None, epoch=None: SimpleNamespace(
        status=200, ok=True, text="an unrelated but genuine news story about world events"))

    def unexpected(*args, **kwargs):
        raise AssertionError("liveness must not consult the search index")

    monkeypatch.setattr(fwd, "in_index", unexpected)
    assert fwd._persistence_status(entry, briefs_by_id, epoch=5, judge_fn=None) == "alive"


def test_persistence_detects_outlet_specific_paid_content_swap(monkeypatch):
    entry = SimpleNamespace(url="https://example.com/story", brief_id="b1")
    registry = OutletRegistry.from_dict({
        "version_id": 1,
        "outlets": [{
            "outlet_id": "example",
            "tier": 1,
            "domains": ["example.com"],
            "paid_markers": ["Commercial Feature"],
        }],
    })
    monkeypatch.setattr(fwd, "fetch_article", lambda url, registry=None, epoch=None: SimpleNamespace(
        status=200, ok=True, text="Commercial Feature for Example Corp",
    ))

    assert fwd._persistence_status(
        entry, {"b1": {"id": "b1"}}, epoch=5, judge_fn=None, registry=registry,
    ) == "dead"
