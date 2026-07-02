from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.evidence import clean_evidence, evidence_hash
from herald.validator.news.commit_index import CommitIndex
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.reward import score_claims, winning_articles
from herald.validator.utils.config import HERALD_ATTR_MULT, HERALD_BASE_PAYOUT_USD

REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [
        {"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"]},
        {"outlet_id": "guardian", "tier": 2, "domains": ["www.theguardian.com"]},
    ],
})
BRIEFS = [{"id": "b1", "kind": "standing"}]


def claim(outlet, url, hotkey, **over):
    fields = dict(
        brief_id="b1", target_outlet_id=outlet, article_url=url,
        claimer_hotkey=hotkey, nonce="n", bond_atto=10**21, version_id=1,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def onchain(c):
    return encode(commit_hash(
        brief_id=c.brief_id, target_outlet_id=c.target_outlet_id,
        claimer_hotkey=c.claimer_hotkey, nonce=c.nonce,
        bond_atto=c.bond_atto, version_id=c.version_id,
        pre_hash=getattr(c, "pre_hash", "") or ""))


def with_text(c, text):
    ev = clean_evidence({"text": text})
    c.pre_hash = evidence_hash(ev)
    c.evidence_text = ev["text"]
    return c


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
        {1: "hkA", 2: "hkB"}, {1: 5000.0, 2: 5000.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == HERALD_BASE_PAYOUT_USD * 1.0 * HERALD_ATTR_MULT[0]
    assert usd[2] == HERALD_BASE_PAYOUT_USD * 0.5 * HERALD_ATTR_MULT[0]


def test_same_url_earliest_commit_wins():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    c2 = claim("nyt", "https://www.nytimes.com/a", "hkB")
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkB": (onchain(c2), 50)})    # B committed earlier
    idx.observe({"hkA": (onchain(c1), 100)})
    usd = score_claims(
        {1: [c1], 2: [c2]}, {"hkA": onchain(c1), "hkB": onchain(c2)}, idx,
        {1: "hkA", 2: "hkB"}, {1: 5000.0, 2: 5000.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[2] == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0] and usd[1] == 0.0


DRAFT = ("Herald announced its public pilot today, saying earned coverage should be provable "
         "not promised, and that miners are paid only for oracle-verified articles.")


def live_body(text):
    return lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000,
                                     final_url=u, text=text)


def test_text_proof_beats_earlier_bare_commit():
    # hkB committed earlier but bare; hkA committed later WITH the draft that ran — hkA wins.
    c1 = with_text(claim("nyt", "https://www.nytimes.com/a", "hkA"), DRAFT)
    c2 = claim("nyt", "https://www.nytimes.com/a", "hkB")
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkB": (onchain(c2), 50)})
    idx.observe({"hkA": (onchain(c1), 100)})
    usd = score_claims(
        {1: [c1], 2: [c2]}, {"hkA": onchain(c1), "hkB": onchain(c2)}, idx,
        {1: "hkA", 2: "hkB"}, {1: 5000.0, 2: 5000.0}, BRIEFS, REGISTRY,
        fetch_fn=live_body("Intro. " + DRAFT), search_fn=indexed)
    assert usd[1] == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[2] and usd[2] == 0.0


def test_shared_text_collision_demotes_both():
    # Two miners committed the same (public press-release) text: proves the campaign, not either
    # miner — both demote to level 1 and the earliest commit wins at the level-1 multiplier.
    c1 = with_text(claim("nyt", "https://www.nytimes.com/a", "hkA"), DRAFT)
    c2 = with_text(claim("nyt", "https://www.nytimes.com/a", "hkB", nonce="n2"), DRAFT)
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkB": (onchain(c2), 50)})    # B committed earlier
    idx.observe({"hkA": (onchain(c1), 100)})
    winners = winning_articles(
        {1: [c1], 2: [c2]}, {"hkA": onchain(c1), "hkB": onchain(c2)}, idx,
        {1: "hkA", 2: "hkB"}, {1: 5000.0, 2: 5000.0}, BRIEFS, REGISTRY,
        fetch_fn=live_body("Intro. " + DRAFT), search_fn=indexed)
    assert len(winners) == 1
    w = winners[0]
    assert w.hotkey == "hkB" and w.level == 1
    assert w.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[1]


def test_bad_commitment_scores_zero():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")
    usd = score_claims(
        {1: [c1]}, {"hkA": "HRLD1|bad"}, index_for({"hkA": "HRLD1|bad"}),
        {1: "hkA"}, {1: 5000.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0


def test_unknown_brief_skipped():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA", brief_id="ghost")
    usd = score_claims(
        {1: [c1]}, {"hkA": onchain(c1)}, index_for({"hkA": onchain(c1)}),
        {1: "hkA"}, {1: 5000.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0


def test_unbacked_bond_not_paid():
    c1 = claim("nyt", "https://www.nytimes.com/a", "hkA")  # big bond, tiny stake
    usd = score_claims(
        {1: [c1]}, {"hkA": onchain(c1)}, index_for({"hkA": onchain(c1)}),
        {1: "hkA"}, {1: 1.0}, BRIEFS, REGISTRY, fetch_fn=live, search_fn=indexed)
    assert usd[1] == 0.0  # bond (1000 alpha) exceeds the 1-alpha stake
