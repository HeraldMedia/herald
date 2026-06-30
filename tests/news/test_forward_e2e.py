from types import SimpleNamespace

import pytest

from herald.commit import commit_hash, encode
from herald.validator.news import fetch as fetchmod
from herald.validator.news import forward as fwd
from herald.validator.news import search as searchmod

BRIEFS = [{"id": "b1", "boost": 1.0}]


def make_claim(outlet, url, hotkey):
    return SimpleNamespace(
        brief_id="b1", target_outlet_id=outlet, article_url=url,
        claimer_hotkey=hotkey, nonce="n", bond_atto=1000, version_id=1,
    )


def onchain(c):
    return encode(commit_hash(
        brief_id=c.brief_id, target_outlet_id=c.target_outlet_id,
        claimer_hotkey=c.claimer_hotkey, nonce=c.nonce,
        bond_atto=c.bond_atto, version_id=c.version_id))


def make_self(claim_by_uid, commitments, block=1000):
    captured = {}

    async def fake_dendrite(axons, synapse, deserialize, timeout):
        return [SimpleNamespace(claims=[claim_by_uid[axons[0]]])]

    self = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69),
        subtensor=SimpleNamespace(
            get_all_commitments=lambda netuid: commitments,
            get_current_block=lambda: block,
        ),
        metagraph=SimpleNamespace(
            hotkeys={1: "hkA", 2: "hkB"},
            axons={1: 1, 2: 2},
            alpha_stake={1: 1.0, 2: 1.0},
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
    monkeypatch.setattr(fwd, "VEST_EPOCHS", 2)


@pytest.mark.asyncio
async def test_forward_vests_first_installment(monkeypatch):
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")   # tier 1 -> 500
    c2 = make_claim("techcrunch", "https://techcrunch.com/b", "hkB")  # tier 2 -> 250
    self, captured = make_self({1: c1, 2: c2}, {"hkA": onchain(c1), "hkB": onchain(c2)})

    await fwd.forward(self)

    rewards = dict(zip(captured["uids"], captured["rewards"]))
    assert rewards[1] == pytest.approx(250.0)  # 500 / VEST_EPOCHS(2)
    assert rewards[2] == pytest.approx(125.0)  # 250 / 2


@pytest.mark.asyncio
async def test_clawback_and_slash_when_article_disappears(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")
    commitments = {"hkA": onchain(c1)}
    self, captured = make_self({1: c1, 2: c1}, commitments)

    # cycle 1: article live -> first installment vests
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == pytest.approx(250.0)

    # cycle 2: article gone -> clawback, slash, nothing paid
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (404, url, b""))
    await fwd.forward(self)
    assert dict(zip(captured["uids"], captured["rewards"]))[1] == 0.0
    assert self._slash.is_slashed("hkA", self.subtensor.get_current_block() // fwd.EPOCH_LEN)
