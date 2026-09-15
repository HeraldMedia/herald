import json
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron
from herald.validator.news import forward as fwd
from herald.validator.news import state as statemod
from herald.validator.news.pricing import PricingError
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.state import HeraldState
from herald.validator.news.url import article_id
from neurons.validator import Validator

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
UPLOADED = int(datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc).timestamp())
DRAFT = ("Quillmere Communications said on Wednesday that the subnet pilot now accepts articles from "
         "PR firms and independent journalists. Each contributor uploads the text they plan to publish "
         "and adds the link after the story is live. Validators fetch the article, confirm the outlet "
         "and the publication date, and check that the uploaded text appears in the published story.")
BODY = "Subnet pilot opens to contributors\n" + DRAFT
# A second contributor's draft that no fetched page contains.
OTHER_DRAFT = ("Wintergreen Analytics said its quarterly survey of 400 newsrooms found that most editors "
               "now review disclosures twice before publication. The survey also recorded a sharp rise in "
               "requests for original data, which editors said makes a pitch far more likely to be "
               "covered. The full Wintergreen report will be released to subscribers next Thursday.")


def row(submission_id, url, brief_id="b1", draft_text=DRAFT, uploaded_ts=UPLOADED):
    return {"submission_id": submission_id, "network": "finney", "netuid": 69,
            "brief_id": brief_id, "url": url, "draft_text": draft_text, "uploaded_ts": uploaded_ts}


def draft_fragments(draft):
    """Pieces of a draft that must never be published, saved or logged."""
    words = draft.split()
    pieces = [" ".join(words[i:i + 6]) for i in range(0, len(words) - 5, 6)]
    return [draft.split()[0]] + pieces


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
    monkeypatch.setattr("herald.validator.utils.config.HERALD_INCENTIVE_HOTKEY", STAR)

    # One day of miner emission is worth $1000.
    env.price = {"alpha_tao": 0.004, "alpha_out": 1.0, "ratio": 1.0, "daily_miner_alpha": 1000.0,
                 "tao_usd": 250.0, "daily_usd": 1000.0}
    env.price_calls = 0

    def price(subtensor, netuid, block):
        env.price_calls += 1
        if isinstance(env.price, Exception):
            raise env.price
        return dict(env.price)

    monkeypatch.setattr(fwd, "daily_miner_usd", price)
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


def burns(env):
    return [line for line in env.logs if line.startswith("INCENTIVE_BURN")]


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
    # $250 payable against $1000 of daily miner emission: a quarter to the incentive UID.
    assert env.updates == [([0, UID_STAR], [0.75, 0.25])]
    assert self.scores.tolist() == [0.75, 0.0, 0.25]
    assert env.commitment_reads == 0 and env.price_calls == 1
    assert (f"INCENTIVE_WEIGHT epoch={epoch} w=0.250000 payable_usd=250.000000 daily_usd=1000.000000 "
            f"alpha_tao=0.004000000 tao_usd=250.000000 daily_miner_alpha=1000.000000 "
            f"uid_star={UID_STAR}") in env.logs

    [snapshot] = env.snapshots
    assert snapshot["epoch"] == epoch
    assert snapshot["state"]["rewards"] == [
        {"uid": UID_STAR, "hotkey": STAR, "reward_microusd": 250_000_000}]
    assert snapshot["state"]["weights"] == [{"uid": 0, "hotkey": "hkOwner", "weight_u16": 49151},
                                            {"uid": UID_STAR, "hotkey": STAR, "weight_u16": 16384}]
    [article] = snapshot["state"]["articles"]
    assert (article["hotkey"], article["reveal"]) == (STAR, {"submission_id": "sub-1"})
    assert article["earned_microusd"] == 250_000_000
    [[published]] = env.results
    assert published["reveal"] == {"submission_id": "sub-1"}


@pytest.mark.asyncio
async def test_drafts_never_reach_published_results_snapshots_logs_or_the_state_file(env, tmp_path):
    env.rows = [row("sub-1", URL_A), row("sub-2", URL_B, draft_text=OTHER_DRAFT)]
    self = make_validator(env)
    self.config.neuron.full_path = str(tmp_path)

    await fwd.forward(self)
    next_epoch(env)
    await fwd.forward(self)

    assert "SUBMISSION_RESULT sub-1 ok" in env.logs
    assert "SUBMISSION_RESULT sub-2 draft_mismatch" in env.logs
    vesting = self.herald_state.vesting
    assert vesting.entry(article_id(URL_A)).reveal == {"submission_id": "sub-1"}
    assert not vesting.has(article_id(URL_B))
    assert len(env.results) == 2 and len(env.snapshots) == 2
    assert [item["reveal"] for rows in env.results for item in rows] == [{"submission_id": "sub-1"}] * 2

    published = json.dumps([env.results, env.snapshots], sort_keys=True)
    saved = "\n".join(path.read_text(encoding="utf-8") for path in sorted(tmp_path.rglob("*")) if path.is_file())
    logged = "\n".join(env.logs)
    assert "sub-1" in published and "sub-1" in saved
    for draft in (DRAFT, OTHER_DRAFT):
        for fragment in draft_fragments(draft):
            for text in (published, saved, logged):
                assert fragment.lower() not in text.lower()


@pytest.mark.asyncio
async def test_rows_without_a_valid_draft_or_upload_time_are_not_verified(env):
    after_chain_time = int(NOW.timestamp()) + 1
    env.rows = [row("sub-1", URL_A, draft_text="Too short to be an article."),
                row("sub-2", URL_B, uploaded_ts=after_chain_time),
                row("sub-3", URL_B + "-follow-up")]
    self = make_validator(env)

    await fwd.forward(self)

    assert "Submissions feed: 3 row(s), 1 valid, 1 to verify" in env.logs
    assert [line for line in env.logs if line.startswith("SUBMISSION_RESULT")] == ["SUBMISSION_RESULT sub-3 ok"]


@pytest.mark.asyncio
async def test_article_published_before_the_draft_upload_day_does_not_vest(env):
    upload_next_day = int(datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc).timestamp())
    env.rows = [row("sub-1", URL_A, uploaded_ts=upload_next_day)]
    self = make_validator(env)

    await fwd.forward(self)

    assert results_for(env, "published_before_upload") == ["SUBMISSION_RESULT sub-1 published_before_upload"]
    assert self.herald_state.vesting.to_dict()["entries"] == {}


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
    assert any(line.startswith("INCENTIVE_WEIGHT") and " w=0.100000 payable_usd=100.000000 " in line
               for line in env.logs)
    [brief_row] = env.snapshots[0]["state"]["briefs"]
    assert (brief_row["pool_spent_microusd"], brief_row["pool_remaining_microusd"]) == (100_000_000, 0)

    next_epoch(env)
    await fwd.forward(self)
    assert self.herald_state.pool_spent == {"b1": pytest.approx(100.0)}
    assert any(line.startswith("INCENTIVE_WEIGHT") and " w=0.000000 payable_usd=0.000000 " in line
               for line in env.logs)
    assert env.updates[-1] == ([0], [1.0])
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
    assert (env.commitment_reads, env.feed_calls, env.registry_calls, env.price_calls) == (0, 0, [], 0)
    assert env.snapshots == [] and env.results == []
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch_of(env)} reason=no_briefs"]


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


# --- incentive weight and burn ----------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("daily_usd, vector", [
    (1000.0, ([0, UID_STAR], [0.75, 0.25])),
    (250.0, ([UID_STAR], [1.0])),
    (100.0, ([UID_STAR], [1.0])),
])
async def test_weights_touch_only_uid_zero_and_the_incentive_uid(env, daily_usd, vector):
    env.price["daily_usd"] = daily_usd
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.2, 0.5, 0.3])

    await fwd.forward(self)

    assert env.updates == [vector]
    assert set(np.flatnonzero(self.scores).tolist()) <= {0, UID_STAR}
    assert {row_["uid"] for row_ in env.snapshots[0]["state"]["weights"]} == set(vector[0])


@pytest.mark.asyncio
async def test_a_failure_after_ledger_changes_burns_and_discards_them(env, monkeypatch, tmp_path):
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 4)
    env.briefs = [{"id": "b1", "kind": "client", "reward_pool": 1000.0,
                   "start_date": "2026-09-01", "end_date": "2026-09-30"}]
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    self.config.neuron.full_path = str(tmp_path)
    state_file = str(tmp_path / "herald_state.json")

    await fwd.forward(self)
    first_epoch = epoch_of(env)
    assert self.herald_state.pool_spent == {"b1": pytest.approx(125.0)}

    # The next pass releases an installment, draws the pool and vests a second article, then fails.
    next_epoch(env)
    failing_epoch = epoch_of(env)
    env.rows = [row("sub-1", URL_A), row("sub-2", URL_B)]

    def unavailable(endpoint, snapshot, hotkey):
        raise RuntimeError("snapshot endpoint unavailable")

    monkeypatch.setattr(fwd, "publish_snapshot", unavailable)
    await fwd.forward(self)

    for state in (self.herald_state, HeraldState.load(state_file)):
        entry = state.vesting.entry(article_id(URL_A))
        assert (entry.remaining, entry.last_release_epoch) == (3, first_epoch)
        assert state.pool_spent == {"b1": pytest.approx(125.0)}
        assert not state.vesting.has(article_id(URL_B))
        assert state.last_scored_epoch == failing_epoch
    assert self.scores.tolist() == [1.0, 0.0, 0.0] and env.updates[-1] == ([0], [1.0])
    assert burns(env) == [f"INCENTIVE_BURN epoch={failing_epoch} reason=error "
                          f"(RuntimeError: snapshot endpoint unavailable)"]

    # The next successful epoch releases the missed installment too.
    monkeypatch.setattr(fwd, "publish_snapshot",
                        lambda endpoint, snapshot, hotkey: env.snapshots.append(snapshot) or True)
    env.rows = [row("sub-1", URL_A)]
    next_epoch(env)
    await fwd.forward(self)
    entry = self.herald_state.vesting.entry(article_id(URL_A))
    assert (entry.remaining, entry.last_release_epoch) == (1, epoch_of(env))
    assert self.herald_state.pool_spent == {"b1": pytest.approx(375.0)}
    assert any(line.startswith(f"INCENTIVE_WEIGHT epoch={epoch_of(env)} w=0.250000 payable_usd=250.000000 ")
               for line in env.logs)


@pytest.mark.asyncio
async def test_pricing_error_burns_the_epoch_and_the_ledger_catches_up_next_epoch(env, monkeypatch,
                                                                                  tmp_path):
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 4)
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    self.config.neuron.full_path = str(tmp_path)
    await fwd.forward(self)
    first_epoch = epoch_of(env)
    saved = HeraldState.load(str(tmp_path / "herald_state.json")).to_dict()

    next_epoch(env)
    env.price = PricingError("TAO/USD price unavailable after 3 attempts")
    feed_calls = env.feed_calls
    await fwd.forward(self)

    assert self.herald_state.last_scored_epoch == epoch_of(env)
    on_disk = HeraldState.load(str(tmp_path / "herald_state.json")).to_dict()
    assert on_disk["vesting"] == saved["vesting"] and on_disk["pool_spent"] == saved["pool_spent"]
    assert on_disk["last_scored_epoch"] == epoch_of(env)
    assert env.updates[-1] == ([0], [1.0]) and self.scores.tolist() == [1.0, 0.0, 0.0]
    assert env.feed_calls == feed_calls and len(env.snapshots) == 1
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch_of(env)} reason=pricing_error "
                          f"(TAO/USD price unavailable after 3 attempts)"]

    env.price = {"alpha_tao": 0.004, "alpha_out": 1.0, "ratio": 1.0, "daily_miner_alpha": 1000.0,
                 "tao_usd": 250.0, "daily_usd": 1000.0}
    next_epoch(env)
    await fwd.forward(self)
    entry = self.herald_state.vesting.entry(article_id(URL_A))
    assert entry.last_release_epoch == epoch_of(env) and entry.remaining == 1  # two installments
    assert first_epoch == epoch_of(env) - 2
    # Two $125 installments against $1000 of daily miner emission.
    assert any(line.startswith(f"INCENTIVE_WEIGHT epoch={epoch_of(env)} w=0.250000 payable_usd=250.000000 ")
               for line in env.logs)
    assert env.updates[-1] == ([0, UID_STAR], [0.75, 0.25])


def _missing_hotkey(env, self, monkeypatch):
    self.metagraph.hotkeys = ["hkOwner", "hkMiner", "hkOther"]


def _wallet_is_incentive_hotkey(env, self, monkeypatch):
    self.wallet.hotkey.ss58_address = STAR


def _incentive_hotkey_at_uid_zero(env, self, monkeypatch):
    self.metagraph.hotkeys = [STAR, "hkMiner", "hkOther"]


def _unset_hotkey(env, self, monkeypatch):
    monkeypatch.setattr(fwd, "HERALD_INCENTIVE_HOTKEY", "")


def _feed_unavailable(env, self, monkeypatch):
    env.rows = None


def _registry_error(env, self, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("registry signature invalid")

    monkeypatch.setattr(fwd, "load_registry", broken)


def _pricing_error(env, self, monkeypatch):
    env.price = PricingError("subnet 69 has no dynamic info at block 1")


def _no_chain_time(env, self, monkeypatch):
    def unavailable(block):
        raise ConnectionError("node unreachable")

    self.subtensor.get_timestamp = unavailable


@pytest.mark.asyncio
@pytest.mark.parametrize("setup, reason, price_calls, feed_calls", [
    (_unset_hotkey, "incentive_hotkey_unset", 0, 0),
    (_missing_hotkey, "incentive_hotkey_not_registered", 0, 0),
    (_wallet_is_incentive_hotkey, "incentive_hotkey_is_validator_hotkey", 0, 0),
    (_incentive_hotkey_at_uid_zero, "incentive_hotkey_at_burn_uid", 0, 0),
    (_no_chain_time, "chain_time_unavailable", 0, 0),
    (_pricing_error, "pricing_error (subnet 69 has no dynamic info at block 1)", 1, 0),
    (_registry_error, "error (RuntimeError: registry signature invalid)", 1, 0),
    (_feed_unavailable, "feed_unavailable", 1, 1),
])
async def test_shared_failures_burn_the_whole_epoch(env, monkeypatch, setup, reason, price_calls,
                                                    feed_calls):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.2, 0.5, 0.3])
    setup(env, self, monkeypatch)

    await fwd.forward(self)

    epoch = epoch_of(env)
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason={reason}"]
    assert env.updates == [([0], [1.0])] and self.scores.tolist() == [1.0, 0.0, 0.0]
    assert self.herald_state.last_scored_epoch == epoch
    assert self.herald_state.vesting.to_dict()["entries"] == {}
    assert (env.price_calls, env.feed_calls) == (price_calls, feed_calls)
    assert env.snapshots == [] and env.results == []

    await fwd.forward(self)  # the burned epoch is not scored again
    assert len(env.updates) == 1 and env.price_calls == price_calls


@pytest.mark.asyncio
async def test_incentive_hotkey_registered_at_a_new_uid_is_paid_there(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    assert env.updates[-1] == ([0, UID_STAR], [0.75, 0.25])

    self.metagraph.hotkeys = ["hkOwner", STAR, "hkReplacement"]
    next_epoch(env)
    await fwd.forward(self)

    assert env.updates[-1] == ([0, 1], [0.75, 0.25])
    assert self.scores.tolist() == [0.75, 0.25, 0.0]
    snapshot = env.snapshots[-1]["state"]
    assert snapshot["rewards"] == [{"uid": 1, "hotkey": STAR, "reward_microusd": 250_000_000}]
    assert [weight["uid"] for weight in snapshot["weights"]] == [0, 1]


@pytest.mark.asyncio
async def test_incentive_hotkey_moving_uid_inside_a_scored_epoch_burns_once(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    epoch = epoch_of(env)
    self.herald_state.last_weight_epoch = epoch  # already submitted

    self.metagraph.hotkeys = ["hkOwner", STAR, "hkReplacement"]
    await fwd.forward(self)

    assert self.scores.tolist() == [1.0, 0.0, 0.0]
    assert self.herald_state.last_weight_epoch == epoch - 1
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason=stale_scores"]

    self.herald_state.last_weight_epoch = epoch  # the burn vector was submitted
    await fwd.forward(self)
    assert self.herald_state.last_weight_epoch == epoch and len(burns(env)) == 1


# --- release cutover --------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("submitted_offset, expected_weight_epoch_offset", [(0, -1), (-2, -2)])
async def test_cutover_discards_per_miner_scores_and_submits_one_burn(env, monkeypatch, tmp_path,
                                                                      submitted_offset,
                                                                      expected_weight_epoch_offset):
    n = 256
    hotkeys = ["hkOwner"] + [f"hk{uid}" for uid in range(1, n)]
    hotkeys[UID_STAR] = STAR
    epoch = epoch_of(env)

    # Files written by the previous release for this epoch: per-miner scores and the Herald ledger.
    old_scores = np.zeros(n, dtype=np.float32)
    old_scores[3], old_scores[157] = 0.5, 0.5
    np.savez(tmp_path / "state.npz", step=4321, scores=old_scores, hotkeys=np.array(hotkeys),
             spec_version=10)
    ledger = HeraldState.fresh()
    ledger.last_scored_epoch = epoch
    ledger.last_weight_epoch = epoch + submitted_offset
    ledger.save(str(tmp_path / "herald_state.json"))

    submissions = []
    receipts = []

    def set_weights(**kwargs):
        submissions.append(kwargs)
        return True, "included"

    validator = object.__new__(Validator)
    validator.config = SimpleNamespace(
        netuid=69,
        neuron=SimpleNamespace(full_path=str(tmp_path), moving_average_alpha=0.1,
                               disable_set_weights=False),
    )
    validator.uid = 1
    validator.wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hk1"))
    validator.metagraph = SimpleNamespace(hotkeys=list(hotkeys), n=n)
    validator.subtensor = SimpleNamespace(
        network="finney", get_current_block=lambda: env.block, get_timestamp=lambda block: NOW,
        min_allowed_weights=lambda netuid: 1, commit_reveal_enabled=lambda netuid: True,
        set_weights=set_weights,
    )
    validator.scores = np.zeros(n, dtype=np.float32)
    validator.check_registered = lambda: None
    validator.should_sync_metagraph = lambda: False
    monkeypatch.setattr(Validator, "block", property(lambda self: env.block))
    monkeypatch.setattr(BaseNeuron, "should_set_weights", lambda self: True)
    monkeypatch.setattr(BaseValidatorNeuron, "_check_pending_weight_commit", lambda self: False)
    monkeypatch.setattr("neurons.validator.publish_weight_receipt",
                        lambda endpoint, receipt, hotkey: receipts.append(receipt))

    # Startup: restore the checkpoint, then the initial sync.
    validator.load_state()
    assert validator.step == 0 and not validator.scores.any()
    validator.sync()
    assert submissions == []

    # First forward step inside the already scored epoch.
    await fwd.forward(validator)
    scores = np.asarray(validator.scores)
    assert np.flatnonzero(scores).tolist() == [0]
    state = validator.herald_state
    assert (state.last_scored_epoch, state.last_weight_epoch) == (epoch, epoch + expected_weight_epoch_offset)
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason=stale_scores"]
    assert env.feed_calls == 0 and env.price_calls == 0

    # The next sync submits the burn once.
    validator.sync()
    [submitted] = submissions
    assert (submitted["uids"], submitted["weights"]) == ([0], [65535])
    assert submitted["version_key"] == 20
    assert state.last_weight_epoch == epoch
    assert HeraldState.load(str(tmp_path / "herald_state.json")).last_weight_epoch == epoch
    assert [receipt["epoch"] for receipt in receipts] == [epoch]

    # A restart later in the same epoch keeps the saved burn vector and does not submit again.
    del validator.herald_state
    validator.scores = np.zeros(n, dtype=np.float32)
    validator.load_state()
    assert np.flatnonzero(validator.scores).tolist() == [0]
    await fwd.forward(validator)
    validator.sync()
    assert len(submissions) == 1 and len(burns(env)) == 1
