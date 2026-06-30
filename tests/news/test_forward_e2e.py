from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from herald.commit import commit_hash, encode
from herald.validator.news import fetch as fetchmod
from herald.validator.news import forward as fwd
from herald.validator.news import search as searchmod
from herald.validator.news import state as statemod
from herald.validator.news.url import article_id as fwd_article_id

BRIEFS = [{"id": "b1", "boost": 1.0}]


def make_claim(outlet, url, hotkey):
    return SimpleNamespace(
        brief_id="b1", target_outlet_id=outlet, article_url=url,
        claimer_hotkey=hotkey, nonce="n", bond_atto=10**21, version_id=1,
    )


def onchain(c):
    return encode(commit_hash(
        brief_id=c.brief_id, target_outlet_id=c.target_outlet_id,
        claimer_hotkey=c.claimer_hotkey, nonce=c.nonce,
        bond_atto=c.bond_atto, version_id=c.version_id))


def make_self(claim_by_uid, commitments, block=1000, monkeypatch=None):
    captured = {}
    block_state = {"v": block}

    # forward reads commitments-with-block from chain; supply {hotkey: (value, block)}
    if monkeypatch is not None:
        monkeypatch.setattr(
            fwd, "get_commitments_with_block",
            lambda subtensor, netuid: {hk: (v, block) for hk, v in commitments.items()},
        )

    async def fake_dendrite(axons, synapse, deserialize, timeout):
        return [SimpleNamespace(claims=[claim_by_uid[axons[0]]])]

    self = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69),
        block_state=block_state,
        subtensor=SimpleNamespace(
            get_current_block=lambda: block_state["v"],
            get_timestamp=lambda b: datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        metagraph=SimpleNamespace(
            hotkeys={1: "hkA", 2: "hkB"},
            axons={1: 1, 2: 2},
            alpha_stake={1: 5000.0, 2: 5000.0},
        ),
        dendrite=fake_dendrite,
        update_scores=lambda rewards, uids: captured.update(rewards=rewards, uids=uids),
    )
    return self, captured


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda q, n: [q])
    monkeypatch.setattr(fwd, "get_briefs", lambda: BRIEFS)
    monkeypatch.setattr(fwd, "get_all_uids", lambda self: [1, 2])
    monkeypatch.setattr(fwd.time, "sleep", lambda *_: None)
    monkeypatch.setattr(statemod, "VEST_EPOCHS", 2)
    monkeypatch.setattr(fwd, "HERALD_TOTAL_DAILY_USD", 0.0)  # no burn; miners split proportionally
    monkeypatch.setattr(fwd, "HERALD_DEAD_CONFIRM_EPOCHS", 1)  # single confirmed-dead slashes (tests)


@pytest.mark.asyncio
async def test_forward_vests_first_installment(monkeypatch):
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")   # tier 1 -> 500
    c2 = make_claim("techcrunch", "https://techcrunch.com/b", "hkB")  # tier 2 -> 250
    self, captured = make_self({1: c1, 2: c2}, {"hkA": onchain(c1), "hkB": onchain(c2)}, monkeypatch=monkeypatch)

    await fwd.forward(self)

    # installments: tier1 500/2=250, tier2 250/2=125 -> proportional weights 2:1
    weights = dict(zip(captured["uids"], captured["rewards"]))
    assert weights[1] == pytest.approx(2 / 3)
    assert weights[2] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_forward_burns_remainder_to_uid0(monkeypatch):
    monkeypatch.setattr(fwd, "get_all_uids", lambda self: [0, 1])
    monkeypatch.setattr(fwd, "HERALD_TOTAL_DAILY_USD", 1000.0)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))

    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")  # tier 1, 500 / 2 = 250
    monkeypatch.setattr(fwd, "get_commitments_with_block",
                        lambda subtensor, netuid: {"hkA": (onchain(c1), 1000)})

    async def fake_dendrite(axons, synapse, deserialize, timeout):
        return [SimpleNamespace(claims=[c1] if axons[0] == 1 else [])]

    captured = {}
    self = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69),
        subtensor=SimpleNamespace(get_current_block=lambda: 1000),
        metagraph=SimpleNamespace(
            hotkeys={0: "burn", 1: "hkA"}, axons={0: 0, 1: 1}, alpha_stake={0: 0.0, 1: 5000.0},
        ),
        dendrite=fake_dendrite,
        update_scores=lambda rewards, uids: captured.update(rewards=rewards, uids=uids),
    )

    await fwd.forward(self)
    w = dict(zip(captured["uids"], captured["rewards"]))
    assert w[1] == pytest.approx(0.25)   # 250 of 1000 daily
    assert w[0] == pytest.approx(0.75)   # remainder burned


@pytest.mark.asyncio
async def test_forward_applies_brief_cap(monkeypatch):
    monkeypatch.setattr(fwd, "get_briefs", lambda: [{"id": "b1", "boost": 1.0, "cap": 0.1}])
    monkeypatch.setattr(fwd, "get_all_uids", lambda self: [0, 1])
    monkeypatch.setattr(fwd, "HERALD_TOTAL_DAILY_USD", 1000.0)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))

    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")  # tier1 500/2=250 installment
    monkeypatch.setattr(fwd, "get_commitments_with_block",
                        lambda subtensor, netuid: {"hkA": (onchain(c1), 1000)})

    async def fake_dendrite(axons, synapse, deserialize, timeout):
        return [SimpleNamespace(claims=[c1] if axons[0] == 1 else [])]

    captured = {}
    self = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69),
        subtensor=SimpleNamespace(get_current_block=lambda: 1000),
        metagraph=SimpleNamespace(
            hotkeys={0: "burn", 1: "hkA"}, axons={0: 0, 1: 1}, alpha_stake={0: 0.0, 1: 5000.0},
        ),
        dendrite=fake_dendrite,
        update_scores=lambda rewards, uids: captured.update(rewards=rewards, uids=uids),
    )

    await fwd.forward(self)
    w = dict(zip(captured["uids"], captured["rewards"]))
    # cap 0.1 * 1000 = 100 caps the 250 installment -> weight 0.1, rest burns
    assert w[1] == pytest.approx(0.1)
    assert w[0] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_claim_organic_article_predating_commit_rejected(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    # article carries a 2020 publish date — long before the (2026) commit
    organic = b'<script>{"datePublished":"2020-05-01T00:00:00Z"}</script>' + b"news " * 200
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, organic))

    await fwd.forward(self)
    rewards = dict(zip(captured["uids"], captured["rewards"]))
    assert rewards.get(1, 0.0) == 0.0  # organic article pays no one


@pytest.mark.asyncio
async def test_future_dated_article_rejected(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    # commit is 2026-01-01; a far-future 2030 date is implausible -> rejected
    future = b'<script>{"datePublished":"2030-05-01T00:00:00Z"}</script>' + b"news " * 200
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, future))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"])).get(1, 0.0) == 0.0


@pytest.mark.asyncio
async def test_uid_reassignment_does_not_pay_new_holder(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))

    await fwd.forward(self)  # cycle 1: placer hkA (uid 1) earns
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(1.0)

    self.metagraph.hotkeys[1] = "hkEVIL"          # uid 1 reassigned to a new hotkey
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    await fwd.forward(self)  # cycle 2: installment must NOT go to the new holder
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == 0.0


@pytest.mark.asyncio
async def test_persistence_clawback_on_value_regression(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)  # cycle 1: valuable
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(1.0)

    # cycle 2: still HTTP 200 but converted to sponsored content -> not valuable -> clawback
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"This is Sponsored Content " * 50))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == 0.0
    epoch = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", epoch)


@pytest.mark.asyncio
async def test_same_epoch_rerun_is_skipped(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)                       # cycle 1 scores
    first = dict(captured)
    captured.clear()
    await fwd.forward(self)                       # same block/epoch -> must skip, not re-score
    assert captured == {}                         # update_scores not called again
    assert first["rewards"] is not None


@pytest.mark.asyncio
async def test_dead_must_be_confirmed_over_consecutive_epochs(monkeypatch):
    monkeypatch.setattr(fwd, "HERALD_DEAD_CONFIRM_EPOCHS", 2)
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)  # cycle 1: alive

    # cycle 2: a single 404 must NOT slash (confirm threshold is 2)
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (404, url, b""))
    await fwd.forward(self)
    e2 = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", e2) is False

    # cycle 3: a second consecutive 404 -> confirmed dead -> slash
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    await fwd.forward(self)
    e3 = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", e3) is True


@pytest.mark.asyncio
async def test_dead_streak_not_double_counted_on_restart(monkeypatch):
    # A crash-restart loses the in-memory same-epoch guard but keeps the persisted
    # dead_streak; re-running the same confirmed-dead epoch must not double-count it.
    monkeypatch.setattr(fwd, "HERALD_DEAD_CONFIRM_EPOCHS", 2)
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)  # cycle 1: alive

    # cycle 2: one confirmed-dead epoch -> dead_streak 1, below threshold 2 -> no slash
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (404, url, b""))
    await fwd.forward(self)
    e2 = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", e2) is False

    # restart: drop the in-memory guard, keep the persisted ledger, re-run the SAME epoch
    del self._last_scored_epoch
    await fwd.forward(self)
    assert self.herald_state.slash.is_slashed("hkA", e2) is False  # still below threshold


@pytest.mark.asyncio
async def test_transient_outage_holds_without_slashing(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)  # cycle 1: pays
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(1.0)

    # cycle 2: provider outage (5xx) -> hold, NOT clawback/slash
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (503, url, b""))
    await fwd.forward(self)
    epoch2 = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", epoch2) is False
    assert self.herald_state.vesting.status(fwd_article_id(c1.article_url)) == "VESTING"

    # cycle 3: recovers -> resumes paying
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_geo_block_451_holds_without_slashing(monkeypatch):
    # 451 (Unavailable For Legal Reasons) is per-validator/jurisdictional and transient; it
    # must hold (no pay), never confirm a removal — even at a confirm threshold of 1.
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)  # cycle 1: alive, pays

    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (451, url, b""))
    await fwd.forward(self)
    epoch2 = self.subtensor.get_current_block() // fwd.EPOCH_LEN
    assert self.herald_state.slash.is_slashed("hkA", epoch2) is False
    assert self.herald_state.vesting.status(fwd_article_id(c1.article_url)) == "VESTING"


@pytest.mark.asyncio
async def test_clawback_and_slash_when_article_disappears(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    commitments = {"hkA": onchain(c1)}
    self, captured = make_self({1: c1, 2: c1}, commitments, monkeypatch=monkeypatch)

    # cycle 1: article live -> only hkA committed, so miner 1 wins all weight
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(1.0)

    # cycle 2: advance past the epoch boundary so the persistence re-check isn't cached
    self.block_state["v"] += fwd.EPOCH_LEN + 1
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (404, url, b""))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == 0.0
    assert self.herald_state.slash.is_slashed("hkA", self.subtensor.get_current_block() // fwd.EPOCH_LEN)


@pytest.mark.asyncio
async def test_failed_cycle_retries_same_epoch(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    self, captured = make_self({1: c1, 2: c1}, {"hkA": onchain(c1)}, monkeypatch=monkeypatch)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    calls = {"n": 0}

    def flaky(rewards, uids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rpc down")        # first attempt fails after partial work
        captured.update(rewards=rewards, uids=uids)

    self.update_scores = flaky
    await fwd.forward(self)                         # attempt 1 throws (swallowed); epoch NOT marked
    assert captured == {}
    await fwd.forward(self)                         # same epoch must RETRY, not skip
    assert captured.get("rewards") is not None
