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
from herald.validator.news.url import article_id, canonicalize
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
         "PR firms and independent PR professionals. Each contributor uploads the text they plan to "
         "publish and adds the link after the story is live. Validators fetch the article, confirm the "
         "outlet and the publication date, and check that the uploaded text appears in the published "
         "story.")
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
    # These tests pin the vectors with the unearned weight burned (HERALD_BURN_UNEARNED=true); the
    # default, full-incentive vectors are tested at the end of this file.
    monkeypatch.setattr(fwd, "HERALD_BURN_UNEARNED", True)
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
    monkeypatch.setattr("neurons.validator.WEIGHT_RESUBMIT_BLOCKS", 180)

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
            f"uid_star={UID_STAR} burn_unearned=true contributor_share_ppb=1000000000") in env.logs

    [snapshot] = env.snapshots
    assert snapshot["epoch"] == epoch
    # The chain already scales the incentive UID's receipt to verified value: all of it is owed.
    assert (snapshot["state"]["burn_unearned"], snapshot["state"]["contributor_share_ppb"]) == (
        True, 1_000_000_000)
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


# --- contested articles and backlog order ---------------------------------------------------------

EARLY = UPLOADED - 3600
LATE = UPLOADED + 3600


def tried(env):
    return [line for line in env.logs if line.startswith("SUBMISSION_RESULT")]


def credited(env):
    return [line for line in env.logs if line.startswith("SUBMISSION_CREDITED")]


def counting_fetch(env, monkeypatch):
    calls = []

    def fetch(url, registry=None, epoch=None):
        calls.append(url)
        return env.pages.get(url, live_page)(url)

    monkeypatch.setattr(fwd, "fetch_article", fetch)
    return calls


@pytest.mark.asyncio
async def test_the_earliest_matching_draft_wins_a_contested_article(env):
    env.rows = [row("sub-linked-first", URL_A, uploaded_ts=LATE),
                row("sub-drafted-first", URL_A + "?utm_source=feed", uploaded_ts=EARLY)]
    self = make_validator(env)

    await fwd.forward(self)

    entry = self.herald_state.vesting.entry(article_id(URL_A))
    assert entry.reveal == {"submission_id": "sub-drafted-first"}
    assert "Submissions feed: 2 row(s), 2 valid, 1 to verify" in env.logs
    assert tried(env) == ["SUBMISSION_RESULT sub-drafted-first ok"]
    assert credited(env) == ["SUBMISSION_CREDITED sub-drafted-first candidate=1/2"]
    assert [item["reveal"] for item in env.results[0]] == [{"submission_id": "sub-drafted-first"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("earlier, reason", [
    ({"draft_text": OTHER_DRAFT}, "draft_mismatch"),
    ({"brief_id": "closed-brief"}, "brief_not_active"),
])
async def test_an_earlier_draft_that_fails_does_not_block_a_later_matching_one(env, earlier, reason):
    env.rows = [row("sub-match", URL_A, uploaded_ts=LATE),
                row("sub-earlier", URL_A, uploaded_ts=EARLY, **earlier)]
    self = make_validator(env)

    await fwd.forward(self)

    assert tried(env) == [f"SUBMISSION_RESULT sub-earlier {reason}", "SUBMISSION_RESULT sub-match ok"]
    assert credited(env) == ["SUBMISSION_CREDITED sub-match candidate=2/2"]
    assert self.herald_state.vesting.entry(article_id(URL_A)).reveal == {"submission_id": "sub-match"}


@pytest.mark.asyncio
async def test_drafts_uploaded_at_the_same_time_go_to_the_lower_submission_id(env):
    env.rows = [row("sub-b", URL_A), row("sub-a", URL_A + "/")]
    self = make_validator(env)

    await fwd.forward(self)

    assert tried(env) == ["SUBMISSION_RESULT sub-a ok"]
    assert credited(env) == ["SUBMISSION_CREDITED sub-a candidate=1/2"]
    assert self.herald_state.vesting.entry(article_id(URL_A)).reveal == {"submission_id": "sub-a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [3, 4])
async def test_only_the_earliest_candidates_up_to_the_cap_are_tried(env, monkeypatch, cap):
    monkeypatch.setattr(fwd, "HERALD_MAX_CANDIDATES_PER_ARTICLE", cap)
    env.rows = [row("sub-4", URL_A, uploaded_ts=UPLOADED + 4)] + [
        row(f"sub-{i}", URL_A, draft_text=OTHER_DRAFT, uploaded_ts=UPLOADED + i) for i in (3, 2, 1)]
    self = make_validator(env)

    await fwd.forward(self)

    assert [line.split()[1] for line in tried(env)] == [f"sub-{i}" for i in range(1, cap + 1)]
    vesting = self.herald_state.vesting
    if cap == 3:
        assert credited(env) == [] and not vesting.has(article_id(URL_A))
    else:
        assert credited(env) == ["SUBMISSION_CREDITED sub-4 candidate=4/4"]
        assert vesting.entry(article_id(URL_A)).reveal == {"submission_id": "sub-4"}


@pytest.mark.asyncio
async def test_each_url_is_fetched_once_per_pass_whatever_the_number_of_candidates(env, monkeypatch):
    calls = counting_fetch(env, monkeypatch)
    env.rows = [row("sub-1", URL_A, draft_text=OTHER_DRAFT, uploaded_ts=EARLY),
                row("sub-2", URL_A + "/", draft_text=OTHER_DRAFT),
                row("sub-3", URL_A + "?utm_source=feed", uploaded_ts=LATE),
                row("sub-4", URL_B)]
    self = make_validator(env)

    await fwd.forward(self)

    assert tried(env) == ["SUBMISSION_RESULT sub-1 draft_mismatch",
                          "SUBMISSION_RESULT sub-2 draft_mismatch",
                          "SUBMISSION_RESULT sub-3 ok", "SUBMISSION_RESULT sub-4 ok"]
    assert credited(env) == ["SUBMISSION_CREDITED sub-3 candidate=3/3",
                             "SUBMISSION_CREDITED sub-4 candidate=1/1"]
    # Verification and the liveness check of both new entries share one fetch per article.
    assert calls == [URL_A, URL_B]

    next_epoch(env)
    await fwd.forward(self)
    assert [canonicalize(url) for url in calls[2:]] == [URL_A, URL_B]  # a new pass fetches again


@pytest.mark.asyncio
async def test_a_fetch_that_raises_is_not_repeated_for_later_candidates(env, monkeypatch):
    calls = []

    def broken(url, registry=None, epoch=None):
        calls.append(url)
        raise RuntimeError("page parser failed")

    monkeypatch.setattr(fwd, "fetch_article", broken)
    env.rows = [row("sub-1", URL_A), row("sub-2", URL_A + "/")]
    self = make_validator(env)

    await fwd.forward(self)

    assert tried(env) == ["SUBMISSION_RESULT sub-1 verify_error", "SUBMISSION_RESULT sub-2 verify_error"]
    assert calls == [URL_A]
    assert not self.herald_state.vesting.has(article_id(URL_A))
    assert self.herald_state.last_scored_epoch == epoch_of(env)


@pytest.mark.asyncio
async def test_an_article_already_vesting_is_skipped_whatever_its_candidates(env):
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)

    next_epoch(env)
    env.rows = [row("sub-0", URL_A + "/", uploaded_ts=EARLY), row("sub-1", URL_A), row("sub-2", URL_B)]
    await fwd.forward(self)

    assert "Submissions feed: 3 row(s), 3 valid, 1 to verify" in env.logs
    assert tried(env) == ["SUBMISSION_RESULT sub-1 ok", "SUBMISSION_RESULT sub-2 ok"]
    assert self.herald_state.vesting.entry(article_id(URL_A)).reveal == {"submission_id": "sub-1"}


@pytest.mark.asyncio
async def test_a_backlog_is_verified_in_upload_order_and_the_cap_counts_articles(env, monkeypatch):
    monkeypatch.setattr(fwd, "HERALD_MAX_SUBMISSIONS_PER_EPOCH", 2)
    urls = [f"https://www.theguardian.com/world/2026/sep/10/story-{i}" for i in range(5)]
    # The feed lists the latest upload first; the earliest article also has an earlier, non-matching
    # draft, and both its rows count as one article against the cap.
    env.rows = [row(f"sub-{i}", url, uploaded_ts=UPLOADED + i * 60)
                for i, url in reversed(list(enumerate(urls)))]
    env.rows.append(row("sub-0b", urls[0] + "/", draft_text=OTHER_DRAFT, uploaded_ts=UPLOADED - 60))
    self = make_validator(env)

    passes = []
    for _ in range(3):
        before = len(tried(env))
        await fwd.forward(self)
        passes.append([line.split(" ", 1)[1] for line in tried(env)[before:]])
        next_epoch(env)

    assert passes == [["sub-0b draft_mismatch", "sub-0 ok", "sub-1 ok"],
                      ["sub-2 ok", "sub-3 ok"],
                      ["sub-4 ok"]]
    assert [line for line in env.logs if line.startswith("Submissions feed")] == [
        "Submissions feed: 6 row(s), 6 valid, 2 to verify",
        "Submissions feed: 6 row(s), 6 valid, 2 to verify",
        "Submissions feed: 6 row(s), 6 valid, 1 to verify",
    ]


@pytest.mark.asyncio
async def test_an_upload_after_an_exact_publication_time_on_the_same_day_does_not_vest(env):
    def exactly_timed(url):
        return SimpleNamespace(ok=True, status=200, final_url=url, text_hash="h", text=BODY,
                               article_text=None, published_ts=PUBLISHED, published_exact=True)

    env.pages[URL_A] = exactly_timed
    # Uploaded on the same UTC day as publication, an hour after the exact publication time.
    env.rows = [row("sub-late-upload", URL_A, uploaded_ts=int(PUBLISHED) + 3600)]
    self = make_validator(env)

    await fwd.forward(self)

    assert tried(env) == ["SUBMISSION_RESULT sub-late-upload published_before_upload"]
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
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason=stale_scores"]
    # The ledger is untouched: the burn is the latest stored vector, and the block-cadence
    # submission sends it like any other.
    assert self.herald_state.last_weight_epoch == epoch

    await fwd.forward(self)
    assert self.herald_state.last_weight_epoch == epoch and len(burns(env)) == 1


# --- release cutover --------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("submitted_offset", [0, -2])
async def test_cutover_discards_per_miner_scores_and_only_ever_submits_the_burn(env, monkeypatch,
                                                                                tmp_path,
                                                                                submitted_offset):
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
    # The chain's weight record for this validator's uid is long past the resubmission interval.
    validator.metagraph = SimpleNamespace(hotkeys=list(hotkeys), n=n,
                                          last_update=np.zeros(n, dtype=np.int64))
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
    assert (state.last_scored_epoch, state.last_weight_epoch) == (epoch, epoch + submitted_offset)
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason=stale_scores"]
    assert env.feed_calls == 0 and env.price_calls == 0

    # The next sync submits the burn.
    validator.sync()
    [submitted] = submissions
    assert (submitted["uids"], submitted["weights"]) == ([0], [65535])
    assert submitted["version_key"] == 20
    assert state.last_weight_epoch == epoch
    assert HeraldState.load(str(tmp_path / "herald_state.json")).last_weight_epoch == epoch
    assert [receipt["epoch"] for receipt in receipts] == [epoch]
    validator.metagraph.last_update[validator.uid] = env.block  # revealed

    # A restart later in the same epoch keeps the saved burn vector and does not submit it again
    # while the chain's record is fresh.
    del validator.herald_state
    validator.scores = np.zeros(n, dtype=np.float32)
    validator.load_state()
    assert np.flatnonzero(validator.scores).tolist() == [0]
    await fwd.forward(validator)
    validator.sync()
    assert len(submissions) == 1 and len(burns(env)) == 1

    # Once the record is stale again, the burn, and only the burn, is re-submitted.
    env.block += 180
    await fwd.forward(validator)
    validator.sync()
    assert [(s["uids"], s["weights"]) for s in submissions] == [([0], [65535]), ([0], [65535])]
    assert (f"Re-submitting Herald epoch {epoch} weights: the chain record for uid 1 is 180 blocks "
            f"old (>= 180)") in env.logs
    assert len(burns(env)) == 1 and env.feed_calls == 0 and env.price_calls == 0
    assert all(not {3, 157} & set(s["uids"]) for s in submissions)


# --- block-cadence weight submission ----------------------------------------------------------------

def chain_validator(env, monkeypatch, tmp_path, hotkeys, *, uid, wallet_hotkey):
    """The real Validator on a fake chain: forward, sync, the base weight gates and set_weights all
    run. Every extrinsic and weight receipt is recorded; nothing leaves the process."""
    chain = SimpleNamespace(submissions=[], receipts=[], pending=False)

    def set_weights(**kwargs):
        chain.submissions.append(kwargs)
        return True, "included"

    def timelocked_commits(netuid):
        return [(wallet_hotkey, env.block, "encrypted", 1)] if chain.pending else []

    validator = object.__new__(Validator)
    validator.step = 1
    validator.uid = uid
    validator.config = SimpleNamespace(
        netuid=69,
        neuron=SimpleNamespace(full_path=str(tmp_path), moving_average_alpha=1.0,
                               disable_set_weights=False, epoch_length=100),
    )
    validator.wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address=wallet_hotkey))
    validator.metagraph = SimpleNamespace(hotkeys=list(hotkeys), n=len(hotkeys),
                                          last_update=np.zeros(len(hotkeys), dtype=np.int64))
    validator.subtensor = SimpleNamespace(
        network="finney", get_current_block=lambda: env.block, get_timestamp=lambda block: NOW,
        min_allowed_weights=lambda netuid: 1, commit_reveal_enabled=lambda netuid: True,
        get_timelocked_weight_commits=timelocked_commits, set_weights=set_weights,
    )
    validator.scores = np.zeros(len(hotkeys), dtype=np.float32)
    validator.hotkeys = list(hotkeys)
    validator.check_registered = lambda: None
    validator.should_sync_metagraph = lambda: False
    monkeypatch.setattr(Validator, "block", property(lambda self: env.block))
    monkeypatch.setattr("neurons.validator.publish_weight_receipt",
                        lambda endpoint, receipt, hotkey: chain.receipts.append(receipt))
    return validator, chain


def vectors(chain):
    return [(kwargs["uids"], kwargs["weights"]) for kwargs in chain.submissions]


def reveal(validator, env):
    """The chain reveals this validator's commit at the current block."""
    validator.metagraph.last_update[validator.uid] = env.block


@pytest.mark.asyncio
async def test_scoring_runs_once_per_epoch_while_its_vector_is_resubmitted(env, monkeypatch,
                                                                           tmp_path):
    monkeypatch.setattr(fwd, "VALIDATOR_STEPS_INTERVAL", 1)  # every step runs a Herald pass
    env.rows = [row("sub-1", URL_A)]
    validator, chain = chain_validator(env, monkeypatch, tmp_path, ["hkOwner", "hkValidator", STAR],
                                       uid=1, wallet_hotkey="hkValidator")
    epoch, first_block = epoch_of(env), env.block

    async def loop_step(blocks):
        env.block += blocks
        await fwd.forward(validator)
        validator.sync()
        validator.step += 1

    await loop_step(0)  # scores the epoch; the chain has no record for this uid yet
    incentive = ([0, UID_STAR], [65535, 21845])
    assert vectors(chain) == [incentive]
    reveal(validator, env)

    for _ in range(3):
        await loop_step(120)  # fresh record: nothing is submitted
        chain.pending = True
        await loop_step(60)   # stale, but a commit is pending reveal
        chain.pending = False
        await loop_step(5)    # stale and nothing pending: the same vector again
        reveal(validator, env)

    assert epoch_of(env) == epoch
    assert vectors(chain) == [incentive] * 4
    assert [receipt["epoch"] for receipt in chain.receipts] == [epoch] * 4
    weight_lines = [line for line in env.logs if line.startswith("INCENTIVE_WEIGHT")]
    assert len(weight_lines) == 1
    assert (env.feed_calls, env.price_calls, len(env.snapshots), len(env.results)) == (1, 1, 1, 1)
    assert validator.herald_state.last_scored_epoch == epoch
    assert validator.herald_state.last_weight_epoch == epoch
    record = f"Herald epoch {epoch} weights: the chain record for uid 1 is"
    assert [line for line in env.logs if line.startswith(("Submitting", "Re-submitting"))] == [
        f"Submitting {record} {first_block} blocks old (>= 180)",
    ] + [f"Re-submitting {record} 185 blocks old (>= 180)"] * 3
    fresh = "Weights for uid 1 are 120 blocks old (< 180); skipping resubmission"
    assert env.logs.count(fresh) == 3
    assert env.logs.count("Weight commitment pending automatic reveal; skipping resubmission") == 3

    # The next epoch is scored once, and its vector is submitted under the same rule.
    next_epoch(env)
    await loop_step(0)
    assert (env.feed_calls, env.price_calls, len(env.snapshots)) == (2, 2, 2)
    assert len(vectors(chain)) == 5 and validator.herald_state.last_weight_epoch == epoch + 1


@pytest.mark.asyncio
async def test_per_miner_scores_kept_by_a_restart_are_only_ever_resubmitted_as_the_burn(
        env, monkeypatch, tmp_path):
    # A score checkpoint from THIS spec version still holding per-miner scores (UIDs 3 and 157) is
    # restored, and the first weight step comes before any Herald pass of this run.
    n = 256
    hotkeys = ["hkOwner"] + [f"hk{uid}" for uid in range(1, n)]
    hotkeys[UID_STAR] = STAR
    epoch = epoch_of(env)
    old_scores = np.zeros(n, dtype=np.float32)
    old_scores[3], old_scores[157] = 0.5, 0.5
    np.savez(tmp_path / "state.npz", step=4321, scores=old_scores, hotkeys=np.array(hotkeys),
             spec_version=Validator.spec_version)
    ledger = HeraldState.fresh()
    ledger.last_scored_epoch = ledger.last_weight_epoch = epoch
    ledger.save(str(tmp_path / "herald_state.json"))

    validator, chain = chain_validator(env, monkeypatch, tmp_path, hotkeys, uid=1,
                                       wallet_hotkey="hk1")
    validator.load_state()
    assert np.flatnonzero(validator.scores).tolist() == [3, 157] and validator.step == 4321

    validator.sync()
    reveal(validator, env)
    env.block += 180
    validator.sync()

    assert vectors(chain) == [([0], [65535]), ([0], [65535])]
    assert sum(line.startswith("WEIGHT_VECTOR_BURN reason=incentive_uid_changed")
               for line in env.logs) == 2
    assert all(not {3, 157} & set(uids) for uids, _ in vectors(chain))
    assert env.feed_calls == 0 and env.price_calls == 0


# --- full incentive: HERALD_BURN_UNEARNED=false, the default ----------------------------------------

@pytest.fixture
def full(env, monkeypatch):
    monkeypatch.setattr(fwd, "HERALD_BURN_UNEARNED", False)
    return env


def fulls(env):
    return [line for line in env.logs if line.startswith("INCENTIVE_FULL")]


@pytest.mark.asyncio
async def test_without_the_burn_a_scored_day_gives_the_incentive_uid_all_the_weight(full):
    env = full
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.1, 0.6, 0.3])

    await fwd.forward(self)

    epoch = epoch_of(env)
    # The article vests exactly as it does with the burn.
    entry = self.herald_state.vesting.entry(article_id(URL_A))
    assert (entry.hotkey, entry.uid, entry.remaining, entry.total_usd) == (STAR, UID_STAR, 1, 500.0)
    assert env.updates == [([UID_STAR], [1.0])]
    assert self.scores.tolist() == [0.0, 0.0, 1.0]
    # $250 payable against $1000 of daily miner emission: a quarter of the receipt is owed.
    assert (f"INCENTIVE_WEIGHT epoch={epoch} w=1.000000 payable_usd=250.000000 daily_usd=1000.000000 "
            f"alpha_tao=0.004000000 tao_usd=250.000000 daily_miner_alpha=1000.000000 "
            f"uid_star={UID_STAR} burn_unearned=false contributor_share_ppb=250000000") in env.logs
    [snapshot] = env.snapshots
    state = snapshot["state"]
    assert (state["burn_unearned"], state["contributor_share_ppb"]) == (False, 250_000_000)
    assert state["rewards"] == [{"uid": UID_STAR, "hotkey": STAR, "reward_microusd": 250_000_000}]
    assert state["weights"] == [{"uid": UID_STAR, "hotkey": STAR, "weight_u16": 65535}]
    assert burns(env) == [] and fulls(env) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("daily_usd, share", [
    (1000.0, 250_000_000), (3000.0, 83_333_333), (250.0, 1_000_000_000), (100.0, 1_000_000_000),
])
async def test_without_the_burn_the_snapshot_states_the_share_owed_capped_at_1e9(full, daily_usd,
                                                                                share):
    env = full
    env.price["daily_usd"] = daily_usd
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)

    await fwd.forward(self)

    assert env.updates == [([UID_STAR], [1.0])]
    assert env.snapshots[0]["state"]["contributor_share_ppb"] == share
    assert f" burn_unearned=false contributor_share_ppb={share}" in [
        line for line in env.logs if line.startswith("INCENTIVE_WEIGHT")][0]


@pytest.mark.asyncio
async def test_without_the_burn_a_day_with_nothing_payable_owes_nothing_and_keeps_full_weight(full):
    env = full
    env.rows = []
    self = make_validator(env)

    await fwd.forward(self)

    assert env.updates == [([UID_STAR], [1.0])]
    [snapshot] = env.snapshots
    assert (snapshot["state"]["burn_unearned"], snapshot["state"]["contributor_share_ppb"]) == (False, 0)
    assert snapshot["state"]["weights"] == [{"uid": UID_STAR, "hotkey": STAR, "weight_u16": 65535}]
    assert any(line.startswith("INCENTIVE_WEIGHT") and " w=1.000000 payable_usd=0.000000 " in line
               and line.endswith(" contributor_share_ppb=0") for line in env.logs)


@pytest.mark.asyncio
async def test_without_the_burn_empty_briefs_give_the_incentive_uid_all_the_weight(full, monkeypatch):
    env = full
    monkeypatch.setenv("HERALD_REGISTRY_AUTHORITY_HOTKEY", AUTHORITY)
    env.briefs = []
    self = make_validator(env, scores=[0.2, 0.5, 0.3])

    await fwd.forward(self)

    epoch = epoch_of(env)
    assert env.updates == [([UID_STAR], [1.0])]
    assert self.scores.tolist() == [0.0, 0.0, 1.0]
    assert self.herald_state.last_scored_epoch == epoch
    assert (env.commitment_reads, env.feed_calls, env.registry_calls, env.price_calls) == (0, 0, [], 0)
    assert env.snapshots == [] and env.results == []
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch} reason=no_briefs uid_star={UID_STAR}"]
    assert burns(env) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("setup, reason, price_calls, feed_calls", [
    (_no_chain_time, "chain_time_unavailable", 0, 0),
    (_pricing_error, "pricing_error (subnet 69 has no dynamic info at block 1)", 1, 0),
    (_registry_error, "error (RuntimeError: registry signature invalid)", 1, 0),
    (_feed_unavailable, "feed_unavailable", 1, 1),
])
async def test_without_the_burn_a_failed_day_still_gives_the_incentive_uid_all_the_weight(
        full, monkeypatch, setup, reason, price_calls, feed_calls):
    env = full
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.2, 0.5, 0.3])
    setup(env, self, monkeypatch)

    await fwd.forward(self)

    epoch = epoch_of(env)
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch} reason={reason} uid_star={UID_STAR}"]
    assert burns(env) == []
    assert env.updates == [([UID_STAR], [1.0])] and self.scores.tolist() == [0.0, 0.0, 1.0]
    assert self.herald_state.last_scored_epoch == epoch
    assert self.herald_state.vesting.to_dict()["entries"] == {}
    assert (env.price_calls, env.feed_calls) == (price_calls, feed_calls)
    assert env.snapshots == [] and env.results == []

    await fwd.forward(self)  # the failed epoch is not scored again, and its vector stays
    assert len(env.updates) == 1 and env.price_calls == price_calls and len(fulls(env)) == 1


@pytest.mark.asyncio
async def test_without_the_burn_a_failure_after_ledger_changes_discards_them_and_keeps_full_weight(
        full, monkeypatch, tmp_path):
    env = full
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 4)
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    self.config.neuron.full_path = str(tmp_path)
    state_file = str(tmp_path / "herald_state.json")
    await fwd.forward(self)
    assert env.snapshots[-1]["state"]["contributor_share_ppb"] == 125_000_000
    saved = HeraldState.load(state_file).to_dict()

    next_epoch(env)

    def unavailable(endpoint, snapshot, hotkey):
        raise RuntimeError("snapshot endpoint unavailable")

    monkeypatch.setattr(fwd, "publish_snapshot", unavailable)
    await fwd.forward(self)

    on_disk = HeraldState.load(state_file).to_dict()
    assert on_disk["vesting"] == saved["vesting"] and on_disk["pool_spent"] == saved["pool_spent"]
    assert on_disk["last_scored_epoch"] == epoch_of(env)
    assert env.updates[-1] == ([UID_STAR], [1.0]) and self.scores.tolist() == [0.0, 0.0, 1.0]
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch_of(env)} reason=error "
                          f"(RuntimeError: snapshot endpoint unavailable) uid_star={UID_STAR}"]

    # The next successful epoch releases the missed installment too: two $125 installments.
    monkeypatch.setattr(fwd, "publish_snapshot",
                        lambda endpoint, snapshot, hotkey: env.snapshots.append(snapshot) or True)
    next_epoch(env)
    await fwd.forward(self)
    assert env.updates[-1] == ([UID_STAR], [1.0])
    assert env.snapshots[-1]["epoch"] == epoch_of(env)
    assert env.snapshots[-1]["state"]["contributor_share_ppb"] == 250_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("setup, reason", [
    (_unset_hotkey, "incentive_hotkey_unset"),
    (_missing_hotkey, "incentive_hotkey_not_registered"),
    (_wallet_is_incentive_hotkey, "incentive_hotkey_is_validator_hotkey"),
    (_incentive_hotkey_at_uid_zero, "incentive_hotkey_at_burn_uid"),
])
@pytest.mark.parametrize("briefs", [STANDING, []])
async def test_without_the_burn_an_unresolvable_incentive_uid_still_burns(full, monkeypatch, setup,
                                                                         reason, briefs):
    env = full
    env.briefs = briefs
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env, scores=[0.2, 0.5, 0.3])
    setup(env, self, monkeypatch)

    await fwd.forward(self)

    epoch = epoch_of(env)
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason={reason}"]
    assert fulls(env) == []
    assert env.updates == [([0], [1.0])] and self.scores.tolist() == [1.0, 0.0, 0.0]
    assert self.herald_state.last_scored_epoch == epoch
    assert (env.price_calls, env.feed_calls) == (0, 0)
    assert env.snapshots == [] and env.results == []


@pytest.mark.asyncio
async def test_without_the_burn_a_scored_epoch_follows_the_incentive_hotkey_uid(full):
    env = full
    env.rows = [row("sub-1", URL_A)]
    self = make_validator(env)
    await fwd.forward(self)
    epoch = epoch_of(env)
    assert env.updates == [([UID_STAR], [1.0])]

    # The incentive hotkey moves to UID 1 inside the scored epoch: the weight moves with it.
    self.metagraph.hotkeys = ["hkOwner", STAR, "hkReplacement"]
    await fwd.forward(self)
    assert self.scores.tolist() == [0.0, 1.0, 0.0]
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch} reason=stale_scores uid_star=1"]
    await fwd.forward(self)
    assert len(fulls(env)) == 1 and burns(env) == []

    # Deregistered: nothing else can be weighted, so the epoch burns.
    self.metagraph.hotkeys = ["hkOwner", "hkMiner", "hkOther"]
    await fwd.forward(self)
    assert self.scores.tolist() == [1.0, 0.0, 0.0]
    assert burns(env) == [f"INCENTIVE_BURN epoch={epoch} reason=stale_scores"]

    # Registered again: all the weight returns to it, without scoring the epoch again.
    self.metagraph.hotkeys = list(HOTKEYS)
    await fwd.forward(self)
    assert self.scores.tolist() == [0.0, 0.0, 1.0]
    assert len(fulls(env)) == 2 and len(burns(env)) == 1
    assert (env.price_calls, env.feed_calls, len(env.snapshots)) == (1, 1, 1)
    assert self.herald_state.last_scored_epoch == epoch


@pytest.mark.asyncio
async def test_without_the_burn_scored_and_failed_days_both_submit_all_the_weight_to_the_incentive_uid(
        full, monkeypatch, tmp_path):
    env = full
    monkeypatch.setattr(fwd, "VALIDATOR_STEPS_INTERVAL", 1)  # every step runs a Herald pass
    env.rows = [row("sub-1", URL_A)]
    validator, chain = chain_validator(env, monkeypatch, tmp_path, ["hkOwner", "hkValidator", STAR],
                                       uid=1, wallet_hotkey="hkValidator")

    await fwd.forward(validator)
    validator.sync()
    assert vectors(chain) == [([UID_STAR], [65535])]
    reveal(validator, env)

    next_epoch(env)
    env.price = PricingError("TAO/USD price unavailable after 3 attempts")
    await fwd.forward(validator)
    validator.sync()

    assert vectors(chain) == [([UID_STAR], [65535])] * 2
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch_of(env)} reason=pricing_error "
                          f"(TAO/USD price unavailable after 3 attempts) uid_star={UID_STAR}"]
    assert [receipt["epoch"] for receipt in chain.receipts] == [epoch_of(env) - 1, epoch_of(env)]


@pytest.mark.asyncio
async def test_without_the_burn_a_release_cutover_submits_all_the_weight_to_the_incentive_uid(
        full, monkeypatch, tmp_path):
    env = full
    monkeypatch.setattr(fwd, "VALIDATOR_STEPS_INTERVAL", 1)
    n = 256
    hotkeys = ["hkOwner"] + [f"hk{uid}" for uid in range(1, n)]
    hotkeys[UID_STAR] = STAR
    epoch = epoch_of(env)
    # An earlier release's per-miner scores for an epoch it already scored.
    old_scores = np.zeros(n, dtype=np.float32)
    old_scores[3], old_scores[157] = 0.5, 0.5
    np.savez(tmp_path / "state.npz", step=4321, scores=old_scores, hotkeys=np.array(hotkeys),
             spec_version=Validator.spec_version)
    ledger = HeraldState.fresh()
    ledger.last_scored_epoch = ledger.last_weight_epoch = epoch
    ledger.save(str(tmp_path / "herald_state.json"))
    validator, chain = chain_validator(env, monkeypatch, tmp_path, hotkeys, uid=1,
                                       wallet_hotkey="hk1")
    validator.load_state()

    await fwd.forward(validator)
    assert np.flatnonzero(validator.scores).tolist() == [UID_STAR]
    assert fulls(env) == [f"INCENTIVE_FULL epoch={epoch} reason=stale_scores uid_star={UID_STAR}"]
    validator.sync()

    assert vectors(chain) == [([UID_STAR], [65535])]
    assert all(not {3, 157} & set(uids) for uids, _ in vectors(chain))
    assert env.feed_calls == 0 and env.price_calls == 0
