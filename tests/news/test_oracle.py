from datetime import datetime
from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.evidence import clean_evidence, evidence_hash
from herald.validator.news.oracle import evaluate_article
from herald.validator.news.registry import OutletRegistry
from herald.validator.utils.config import HERALD_ATTR_MULT, HERALD_BASE_PAYOUT_USD

REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"]}],
})
BRIEF = {"id": "b1"}


def make_claim(**over):
    fields = dict(
        brief_id="b1", target_outlet_id="nyt",
        article_url="https://www.nytimes.com/2026/01/01/world/story",
        claimer_hotkey="5Haaa", nonce="n1", bond_atto=10**21, version_id=1,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def onchain_for(claim):
    return encode(commit_hash(
        brief_id=claim.brief_id, target_outlet_id=claim.target_outlet_id,
        claimer_hotkey=claim.claimer_hotkey, nonce=claim.nonce,
        bond_atto=claim.bond_atto, version_id=claim.version_id,
        pre_hash=getattr(claim, "pre_hash", "") or "",
    ))


def make_evidence_claim(evidence, **over):
    ev = clean_evidence(evidence)
    return make_claim(
        pre_hash=evidence_hash(ev), evidence_text=ev.get("text"),
        evidence_author=ev.get("author"), evidence_window=ev.get("window"), **over,
    )


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
    # A bare commit (no attribution evidence) passes but pays the level-0 multiplier.
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert r.passed and r.reason == "ok" and r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0]
    assert r.evidence["tier"] == 1 and r.evidence["in_index"] is True
    assert r.evidence["attribution_level"] == 0


DRAFT = ("Herald announced its public pilot today, saying earned coverage should be provable "
         "not promised, and that miners are paid only for oracle-verified articles.")


def live_with(text=None, author=None, published=None):
    ts = datetime.fromisoformat(published + "T12:00:00+00:00").timestamp() if published else None
    body = text or "A normal news report about world events."
    return lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000,
                                     final_url=u, text=body, author=author, published_ts=ts)


def test_text_proof_pays_full():
    c = make_evidence_claim({"text": DRAFT})
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text="Intro. " + DRAFT + " Outro."), search_fn=indexed)
    assert r.passed and r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[2]
    assert r.evidence["attribution_level"] == 2


def test_text_proof_misses_grades_level0():
    c = make_evidence_claim({"text": DRAFT})
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text="An unrelated story about football results."),
                         search_fn=indexed)
    assert r.passed and r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0]
    assert r.evidence["attribution_level"] == 0


def test_insider_detail_pays_level1():
    c = make_evidence_claim({"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]})
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(author="Jane Doe", published="2026-07-12"),
                         search_fn=indexed)
    assert r.passed and r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[1]
    assert r.evidence["attribution_level"] == 1


def test_wrong_byline_grades_level0():
    c = make_evidence_claim({"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]})
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(author="John Smith", published="2026-07-12"),
                         search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 0


def test_evidence_hash_mismatch_rejected():
    # Swapping the revealed text post-publication must fail: the pre_hash was fixed at commit.
    c = make_evidence_claim({"text": DRAFT})
    c.evidence_text = "different text scraped from the published article after the fact"
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=c.evidence_text), search_fn=indexed)
    assert not r.passed and r.reason == "evidence_hash_mismatch"


def test_dropping_evidence_breaks_commitment():
    # A commit sealed WITH a pre_hash can't be claimed as a bare commit.
    c = make_evidence_claim({"text": DRAFT})
    onchain = onchain_for(c)
    c.pre_hash = None
    c.evidence_text = None
    r = evaluate_article(c, onchain, REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "commitment_invalid"


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


def test_not_indexed_pays_the_search_floor():
    from herald.validator.utils.config import HERALD_NO_SEARCH_FLOOR

    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=not_indexed)
    assert r.passed and r.evidence["in_index"] is False
    assert r.usd == HERALD_BASE_PAYOUT_USD * HERALD_NO_SEARCH_FLOOR * HERALD_ATTR_MULT[0]


def test_paid_content_rejected_before_search():
    c = make_claim(article_url="https://www.nytimes.com/sponsored/story")
    # registry matches nytimes by domain regardless of path; paid path triggers rejection
    called = []
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live,
                         search_fn=lambda u: called.append(u))
    assert not r.passed and r.reason == "paid_not_real_news" and called == []


def test_topic_mismatch_rejected():
    brief = {"id": "b1", "keywords": ["bittensor"]}
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, brief, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "topic_mismatch"


def test_bond_too_small_rejected():
    c = make_claim(bond_atto=1000)  # far below the required ~750 alpha for a tier-1 $500 reward
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "bond_too_small"


def test_stale_version_rejected():
    c = make_claim(version_id=999)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "stale_version"


def test_hotkey_mismatch_rejected():
    c = make_claim(claimer_hotkey="hkVICTIM")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live,
                         search_fn=indexed, serving_hotkey="hkSERVING")
    assert not r.passed and r.reason == "hotkey_mismatch"
