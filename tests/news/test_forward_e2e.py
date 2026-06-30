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


@pytest.mark.asyncio
async def test_forward_scores_two_miners(monkeypatch):
    c1 = make_claim("nytimes", "https://www.nytimes.com/a", "hkA")   # tier 1
    c2 = make_claim("techcrunch", "https://techcrunch.com/b", "hkB")  # tier 2

    # stub network egress so the real fetch()/in_index() run offline
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"x" * 2000))
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda q, n: [q])

    # stub chain + harness
    monkeypatch.setattr(fwd, "get_briefs", lambda: BRIEFS)
    monkeypatch.setattr(fwd, "get_all_uids", lambda self: [1, 2])
    monkeypatch.setattr(fwd.time, "sleep", lambda *_: None)

    async def fake_dendrite(axons, synapse, deserialize, timeout):
        claim = {1: c1, 2: c2}[axons[0]]
        return [SimpleNamespace(claims=[claim])]

    captured = {}

    self = SimpleNamespace(
        step=0,
        config=SimpleNamespace(netuid=69),
        subtensor=SimpleNamespace(
            get_all_commitments=lambda netuid: {"hkA": onchain(c1), "hkB": onchain(c2)},
            get_current_block=lambda: 1000,
        ),
        metagraph=SimpleNamespace(
            hotkeys={1: "hkA", 2: "hkB"},
            axons={1: 1, 2: 2},
            alpha_stake={1: 1.0, 2: 1.0},
        ),
        dendrite=fake_dendrite,
        update_scores=lambda rewards, uids: captured.update(rewards=rewards, uids=uids),
    )

    await fwd.forward(self)

    rewards = dict(zip(captured["uids"], captured["rewards"]))
    assert rewards[1] == pytest.approx(500.0)   # tier 1 full payout
    assert rewards[2] == pytest.approx(250.0)   # tier 2 half
