from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.validator.news.oracle import evaluate_article
from herald.validator.news.registry import OutletRegistry
from herald.validator.utils.config import HERALD_BASE_PAYOUT_USD

REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"]}],
})
BRIEF = {"id": "b1", "boost": 1.0}


def make_claim(**over):
    fields = dict(
        brief_id="b1", target_outlet_id="nyt",
        article_url="https://www.nytimes.com/2026/01/01/world/story",
        claimer_hotkey="5Haaa", nonce="n1", bond_atto=1000, version_id=1,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def onchain_for(claim):
    return encode(commit_hash(
        brief_id=claim.brief_id, target_outlet_id=claim.target_outlet_id,
        claimer_hotkey=claim.claimer_hotkey, nonce=claim.nonce,
        bond_atto=claim.bond_atto, version_id=claim.version_id,
    ))


def live(_url):
    return SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000,
                           final_url=_url, text="A normal news report about world events.")


def dead(_url):
    return SimpleNamespace(ok=False, status=404, text_hash="", body_len=0, final_url=_url, text="")


def indexed(_url):
    return SimpleNamespace(in_index=True, matched_url=_url, num_results=5, query=_url)


def not_indexed(_url):
    return SimpleNamespace(in_index=False, matched_url=None, num_results=3, query=_url)


def test_happy_path_pays_tier1():
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert r.passed and r.reason == "ok" and r.usd == HERALD_BASE_PAYOUT_USD
    assert r.evidence["tier"] == 1 and r.evidence["in_index"] is True


def test_bad_commitment_rejected():
    c = make_claim()
    r = evaluate_article(c, "HRLD1|deadbeef", REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "commitment_invalid" and r.usd == 0.0


def test_unlisted_outlet_rejected():
    c = make_claim(article_url="https://contentfarm.example/x")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "outlet_not_listed" and r.usd == 0.0


def test_outlet_mismatch_rejected():
    c = make_claim(target_outlet_id="someoneelse")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "outlet_mismatch"


def test_dead_url_rejected_without_search_call():
    c = make_claim()
    called = []
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=dead,
                         search_fn=lambda u: called.append(u))
    assert not r.passed and r.reason == "url_not_live" and r.usd == 0.0
    assert called == []  # early-exit: search never runs


def test_not_indexed_passes_but_zero_usd():
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=not_indexed)
    assert r.passed and r.usd == 0.0 and r.evidence["in_index"] is False


def test_paid_content_rejected_before_search():
    c = make_claim(article_url="https://www.nytimes.com/sponsored/story")
    # registry matches nytimes by domain regardless of path; paid path triggers rejection
    called = []
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live,
                         search_fn=lambda u: called.append(u))
    assert not r.passed and r.reason == "paid_not_real_news" and called == []


def test_topic_mismatch_rejected():
    brief = {"id": "b1", "boost": 1.0, "keywords": ["bittensor"]}
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, brief, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "topic_mismatch"
