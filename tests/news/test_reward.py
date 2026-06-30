from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.validator.news.commit_index import CommitIndex
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


def claim(outlet, url, hotkey, **over):
    fields = dict(
        brief_id="b1", target_outlet_id=outlet, article_url=url,
        claimer_hotkey=hotkey, nonce="n", bond_atto=1000, version_id=1,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def onchain(c):
    return encode(commit_hash(
        brief_id=c.brief_id, target_outlet_id=c.target_outlet_id,
        claimer_hotkey=c.claimer_hotkey, nonce=c.nonce,
        bond_atto=c.bond_atto, version_id=c.version_id))


live = lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000, final_url=u,
                                 text="A normal news report.")
indexed = lambda u: SimpleNamespace(in_index=True, matched_url=u, num_results=5, query=u)


def index_for(commitments, block=100):
    idx = CommitIndex(epoch_len=10)
    idx.observe({hk: (v, block) for hk, v in commitments.items()})
    return idx


def test_two_miners_distinct_outlets_both_paid():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    c2 = claim("guardian", "https://www.theguardian.com/b", "hkB")
    commitments = {"hkA": onchain(c1), "hkB": onchain(c2)}
    usd = score_claims(
        {1: [c1], 2: [c2]}, commitments, index_for(commitments),
        {1: "hkA", 2: "hkB"}, {1: 1.0, 2: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == HERALD_BASE_PAYOUT_USD * 1.0
    assert usd[2] == HERALD_BASE_PAYOUT_USD * 0.5


def test_same_url_earliest_commit_wins():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    c2 = claim("nyt", "https://www.nytimes.com/a", "hkB")
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkB": (onchain(c2), 50)})    # B committed earlier
    idx.observe({"hkA": (onchain(c1), 100)})
    usd = score_claims(
        {1: [c1], 2: [c2]}, {"hkA": onchain(c1), "hkB": onchain(c2)}, idx,
        {1: "hkA", 2: "hkB"}, {1: 1.0, 2: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[2] == HERALD_BASE_PAYOUT_USD and usd[1] == 0.0


def test_bad_commitment_scores_zero():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    usd = score_claims(
        {1: [c1]}, {"hkA": "HRLD1|bad"}, index_for({"hkA": "HRLD1|bad"}),
        {1: "hkA"}, {1: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0


def test_unknown_brief_skipped():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA", brief_id="ghost")
    usd = score_claims(
        {1: [c1]}, {"hkA": onchain(c1)}, index_for({"hkA": onchain(c1)}),
        {1: "hkA"}, {1: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0


def test_unbacked_bond_not_paid():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA", bond_atto=10 ** 19)  # 10 alpha
    usd = score_claims(
        {1: [c1]}, {"hkA": onchain(c1)}, index_for({"hkA": onchain(c1)}),
        {1: "hkA"}, {1: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0  # only 1 alpha staked, bond asserts 10
