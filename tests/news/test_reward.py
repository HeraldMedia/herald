from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.reward import score_claims
from herald.validator.utils.config import HERALD_BASE_PAYOUT_USD

REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [
        {"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"]},
        {"outlet_id": "guardian", "tier": 2, "domains": ["www.theguardian.com"]},
    ],
})
BRIEFS = [{"id": "b1", "boost": 1.0}]


def claim(uid_outlet, url, hotkey, **over):
    fields = dict(
        brief_id="b1", target_outlet_id=uid_outlet, article_url=url,
        claimer_hotkey=hotkey, nonce="n", bond_atto=1000, version_id=1,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def onchain(c):
    return encode(commit_hash(
        brief_id=c.brief_id, target_outlet_id=c.target_outlet_id,
        claimer_hotkey=c.claimer_hotkey, nonce=c.nonce,
        bond_atto=c.bond_atto, version_id=c.version_id))


live = lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000, final_url=u)
indexed = lambda u: SimpleNamespace(in_index=True, matched_url=u, num_results=5, query=u)


def test_two_miners_scored():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    c2 = claim("guardian", "https://www.theguardian.com/b", "hkB")
    usd = score_claims(
        claims_by_uid={1: [c1], 2: [c2]},
        commitments={"hkA": onchain(c1), "hkB": onchain(c2)},
        hotkey_by_uid={1: "hkA", 2: "hkB"},
        briefs=BRIEFS, registry=REGISTRY, fetch_fn=live, search_fn=indexed,
    )
    assert usd[1] == HERALD_BASE_PAYOUT_USD * 1.0
    assert usd[2] == HERALD_BASE_PAYOUT_USD * 0.5


def test_bad_commitment_scores_zero():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    usd = score_claims(
        claims_by_uid={1: [c1]}, commitments={"hkA": "HRLD1|bad"},
        hotkey_by_uid={1: "hkA"}, briefs=BRIEFS, registry=REGISTRY,
        fetch_fn=live, search_fn=indexed,
    )
    assert usd[1] == 0.0


def test_unknown_brief_skipped():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA", brief_id="ghost")
    usd = score_claims(
        claims_by_uid={1: [c1]}, commitments={"hkA": onchain(c1)},
        hotkey_by_uid={1: "hkA"}, briefs=BRIEFS, registry=REGISTRY,
        fetch_fn=live, search_fn=indexed,
    )
    assert usd[1] == 0.0


def test_multiple_claims_sum():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    c2 = claim("guardian", "https://www.theguardian.com/b", "hkA")
    usd = score_claims(
        claims_by_uid={1: [c1, c2]},
        commitments={"hkA": onchain(c1)},  # only c1's commitment is on chain
        hotkey_by_uid={1: "hkA"}, briefs=BRIEFS, registry=REGISTRY,
        fetch_fn=live, search_fn=indexed,
    )
    # c1 valid (tier1=full), c2 commitment mismatch -> 0
    assert usd[1] == HERALD_BASE_PAYOUT_USD * 1.0
