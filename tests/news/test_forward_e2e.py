from types import SimpleNamespace

import pytest

from herald.commit import commit_hash, encode
from herald.validator.news import fetch as fetchmod
from herald.validator.news import forward as fwd
from herald.validator.news import search as searchmod
from herald.validator.news import state as statemod

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
