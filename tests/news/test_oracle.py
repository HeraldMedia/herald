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
        claimer_hotkey="5Haaa", nonce="n1", bond_atto=0, version_id=1,
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


def live_with(text=None, author=None, published=None, article_text=None):
    ts = datetime.fromisoformat(published + "T12:00:00+00:00").timestamp() if published else None
    body = text or "A normal news report about world events."
    return lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000,
                                     final_url=u, text=body, article_text=article_text,
                                     author=author, published_ts=ts)


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


def test_zero_bond_is_eligible():
    c = make_claim(bond_atto=0)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert r.passed


def test_stale_version_rejected():
    c = make_claim(version_id=999)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live, search_fn=indexed)
    assert not r.passed and r.reason == "stale_version"


def test_hotkey_mismatch_rejected():
    c = make_claim(claimer_hotkey="hkVICTIM")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF, fetch_fn=live,
                         search_fn=indexed, serving_hotkey="hkSERVING")
    assert not r.passed and r.reason == "hotkey_mismatch"


BRIEF_KW = {"id": "b1", "keywords": ["bittensor"]}
SNAPSHOT = ("The Bittensor media subnet Herald opened its public pilot this week. " * 4).strip()
ARTICLE_SENTENCES = [
    "The Bittensor media subnet Herald opened its public pilot this week after months of rehearsal.",
    "Miners are paid only for articles that pass an automated verification oracle run by validators.",
    "Earned coverage should be provable, not promised, the team said in its launch note on Tuesday.",
    "The registry of approved outlets is signed offline and anchored on chain for auditability.",
]
ARTICLE = " ".join(ARTICLE_SENTENCES)
PAGE_WITH_CHROME = "Site navigation: World Business Technology. " + ARTICLE + " Footer: About Contact."


def test_topic_is_checked_on_the_fetched_article_not_the_snapshot():
    # The snapshot names the brief keyword; the page we fetched does not. The snapshot still clears
    # the 0.5 anchor floor, and topic is decided on the page we fetched.
    page = SNAPSHOT.replace("Bittensor", "the network")
    c = make_claim(snapshot_text=SNAPSHOT)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=page), search_fn=indexed)
    assert 0.5 <= r.evidence["snapshot_anchor"] < 0.6
    assert not r.passed and r.reason == "topic_mismatch"
    assert r.evidence["topic_match"] is False


def test_snapshot_above_the_anchor_floor_is_scored_on_the_fetched_article():
    # The miner saw a page variant without the keyword; the page we fetched carries it. About half
    # of the snapshot differs, which the 0.5 floor tolerates, and the claim passes on our fetch.
    c = make_claim(snapshot_text=SNAPSHOT.replace("Bittensor", "Tao"))
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=SNAPSHOT), search_fn=indexed)
    assert r.passed and 0.5 <= r.evidence["snapshot_anchor"] < 0.6
    assert r.evidence["topic_match"] is True


def test_attribution_is_graded_on_the_fetched_article_not_the_snapshot():
    # The committed quote is in the snapshot, but the page we fetched paraphrases it. The snapshot
    # still clears the anchor; the quote is graded against the page we fetched, so no text proof.
    quote = ARTICLE_SENTENCES[2]
    page = ARTICLE.replace(quote, "The team framed verification as central in its launch note.")
    c = make_evidence_claim({"text": quote}, snapshot_text=ARTICLE)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=page), search_fn=indexed)
    assert r.passed and r.evidence["snapshot_anchor"] >= 0.5
    assert r.evidence["attribution_level"] == 0
    assert r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0]


def test_attribution_reads_only_the_fetched_article():
    # The snapshot also carries the committed draft. Topic passes on the page we fetched, and
    # attribution is graded on that page too.
    c = make_evidence_claim({"text": DRAFT}, snapshot_text=ARTICLE + " " + DRAFT)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=ARTICLE), search_fn=indexed)
    assert r.passed and r.evidence["snapshot_anchor"] >= 0.5
    assert r.evidence["topic_match"] is True
    assert r.evidence["attribution_level"] == 0


def test_claim_without_a_snapshot_is_topic_checked_on_the_extracted_article():
    # Only the headline outside the extracted article body names the brief keyword. With an article
    # body extracted, topic is decided on that body; with none, on the page text.
    article = ARTICLE.replace("Bittensor", "decentralised")
    page = "Bittensor subnet opens its public pilot. " + article + " Footer: About Contact."
    c = make_claim()
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=page, article_text=article), search_fn=indexed)
    assert not r.passed and r.reason == "topic_mismatch"
    assert r.evidence["topic_match"] is False
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=page), search_fn=indexed)
    assert r.passed and r.evidence["topic_match"] is True


def test_claim_without_a_snapshot_is_attribution_graded_on_the_extracted_article():
    # The committed quote appears only as a pull quote outside the extracted article body. With an
    # article body extracted there is no text proof; with none, the quote is graded on the page text.
    quote = ARTICLE_SENTENCES[2]
    article = " ".join(ARTICLE_SENTENCES[:2] + ARTICLE_SENTENCES[3:])
    page = quote + " " + article
    c = make_evidence_claim({"text": quote})
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=page, article_text=article), search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 0
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=page), search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 2


def test_committed_text_that_appears_in_the_fetched_article_reaches_level2():
    # An honest claim: the snapshot carries a banner our extraction leaves out, and the committed
    # quote really is in the article body we fetched.
    quote = ARTICLE_SENTENCES[2]
    c = make_evidence_claim({"text": quote}, snapshot_text="Subscribe for daily briefings. " + ARTICLE)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF_KW,
                         fetch_fn=live_with(text=PAGE_WITH_CHROME, article_text=ARTICLE),
                         search_fn=indexed)
    assert r.passed and r.evidence["snapshot_anchor"] >= 0.5
    assert r.evidence["attribution_level"] == 2
    assert r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[2]


def test_snapshot_below_the_anchor_rejects_even_when_the_fetch_would_pass():
    # The page we fetched carries the committed draft and would grade a text proof; a snapshot that
    # does not match that page still rejects the claim for this pass.
    c = make_evidence_claim({"text": DRAFT},
                            snapshot_text="A completely different page about football results.")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text="Intro. " + DRAFT + " Outro."), search_fn=indexed)
    assert not r.passed and r.reason == "snapshot_mismatch"
    assert r.evidence["snapshot_anchor"] < 0.5


def test_paid_checks_read_the_fetched_article_for_full_body_outlets():
    # Both the disclosure rules and the LLM fallback read the article we fetched, never the snapshot.
    from herald.validator.news.judge import PAID_QUESTION

    asked = []

    def judge_fn(question, text):
        asked.append((question, text))
        return None  # no verdict: the rules decide

    c = make_claim(snapshot_text=ARTICLE + " Sponsored content by the client.")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=PAGE_WITH_CHROME, article_text=ARTICLE),
                         search_fn=indexed, judge_fn=judge_fn)
    assert r.passed and r.evidence["paid"] is False
    assert PAID_QUESTION in [question for question, _ in asked]
    assert all(text == ARTICLE for _, text in asked)


def test_snapshot_mismatch_rejected_this_pass():
    c = make_claim(snapshot_text=SNAPSHOT)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text="A completely different page about football."),
                         search_fn=indexed)
    assert not r.passed and r.reason == "snapshot_mismatch"


def test_paid_marker_in_own_fetch_still_rejects_snapshot_claims():
    # A miner can't launder a sponsored page by snapshotting it without the disclosure label.
    c = make_claim(snapshot_text=SNAPSHOT)
    paid_page = SNAPSHOT + " Sponsored content by the client."
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=live_with(text=paid_page), search_fn=indexed)
    assert not r.passed and r.reason == "paid_not_real_news"


def test_paid_marker_outside_article_does_not_reject_snapshot_claim():
    c = make_claim(snapshot_text=SNAPSHOT)
    full_page = SNAPSHOT + " Sponsored content directory in the site footer."
    r = evaluate_article(
        c, onchain_for(c), REGISTRY, BRIEF,
        fetch_fn=live_with(text=full_page, article_text=SNAPSHOT), search_fn=indexed,
    )
    assert r.passed


def test_paid_marker_inside_article_still_rejects_snapshot_claim():
    c = make_claim(snapshot_text=SNAPSHOT)
    article = SNAPSHOT + " Sponsored content by the client."
    r = evaluate_article(
        c, onchain_for(c), REGISTRY, BRIEF,
        fetch_fn=live_with(text=article, article_text=article), search_fn=indexed,
    )
    assert not r.passed and r.reason == "paid_not_real_news"


LEAD = "The lead paragraph carries a distinctive verbatim sentence about the world summit today."


def excerpt_with(topic_word="summit", author=None, published=None):
    """A fetch_fn returning an AUTHORITATIVE excerpt (body_kind='excerpt'), as the api:* adapters do:
    `text` is the lead paragraph (the anchor target), `topic_text` the unfakeable topic blob."""
    ts = datetime.fromisoformat(published + "T12:00:00+00:00").timestamp() if published else None
    topic_text = f"Headline mentioning the {topic_word}. {LEAD}"
    return lambda u: SimpleNamespace(ok=True, status=200, text_hash="", body_len=len(LEAD),
                                     final_url=u, text=LEAD, body_kind="excerpt",
                                     topic_text=topic_text, author=author, published_ts=ts)


def test_excerpt_mode_anchors_lead_in_snapshot():
    # api outlet: the validator holds only the lead paragraph; the miner snapshot carries the body
    # and must CONTAIN the lead (reverse anchor). Topic is judged on the authoritative topic_text.
    snapshot = "Opening sentence. " + LEAD + " Then more body about the summit and its aftermath here."
    c = make_claim(snapshot_text=snapshot)
    r = evaluate_article(c, onchain_for(c), REGISTRY, {"id": "b1", "keywords": ["summit"]},
                         fetch_fn=excerpt_with(topic_word="summit"), search_fn=indexed)
    assert r.passed and r.reason == "ok"
    assert r.evidence["snapshot_anchor"] >= 0.5 and r.evidence["topic_match"] is True


def test_excerpt_mode_requires_a_snapshot():
    c = make_claim()  # no snapshot: the validator has no body to check
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=excerpt_with(), search_fn=indexed)
    assert not r.passed and r.reason == "snapshot_required"


def test_excerpt_mode_snapshot_without_the_lead_rejected():
    c = make_claim(snapshot_text="An unrelated body that never contains the authoritative lead sentence.")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=excerpt_with(), search_fn=indexed)
    assert not r.passed and r.reason == "snapshot_mismatch"


def test_excerpt_mode_grades_byline_from_authoritative_api():
    # Byline + date come from the API (unfakeable), so level-1 attribution works on a bot-walled
    # outlet the validator could never scrape a byline from.
    snapshot = "Intro. " + LEAD + " Outro paragraph."
    c = make_evidence_claim({"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]},
                            snapshot_text=snapshot)
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=excerpt_with(author="Jane Doe", published="2026-07-12"),
                         search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 1


def test_excerpt_mode_never_grades_a_text_proof():
    # api outlet: the only article body is the claim's own snapshot, so committed text is not
    # graded, even when it also appears in the authoritative lead paragraph.
    c = make_evidence_claim({"text": LEAD}, snapshot_text="Intro. " + LEAD + " Outro paragraph.")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=excerpt_with(), search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 0
    assert r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0]


def test_excerpt_mode_grades_byline_and_window_when_text_is_also_committed():
    evidence = {"text": LEAD, "author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]}
    c = make_evidence_claim(evidence, snapshot_text="Intro. " + LEAD + " Outro paragraph.")
    r = evaluate_article(c, onchain_for(c), REGISTRY, BRIEF,
                         fetch_fn=excerpt_with(author="Jane Doe", published="2026-07-12"),
                         search_fn=indexed)
    assert r.passed and r.evidence["attribution_level"] == 1
    assert r.usd == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[1]
