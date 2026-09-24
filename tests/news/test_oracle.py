import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from herald.validator.news import oracle
from herald.validator.news.fetch import _parse_published_value
from herald.validator.news.oracle import published_after_upload, verify_article
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.topic_match import topic_matched
from herald.validator.news.url import article_id
from herald.validator.utils import config


def ts(*parts):
    return datetime(*parts, tzinfo=timezone.utc).timestamp()


REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [
        {"outlet_id": "guardian", "tier": 1, "domains": ["www.theguardian.com"]},
        {"outlet_id": "techcrunch", "tier": 2, "domains": ["techcrunch.com"], "fetch": "proxy:premium"},
        {"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"], "fetch": "api:nyt"},
        {"outlet_id": "paused", "tier": 1, "domains": ["paused.example.com"], "fetch": "disabled"},
    ],
})
URL = "https://www.theguardian.com/world/2026/sep/08/subnet-pilot"
BRIEF = {"id": "b1", "kind": "client", "reward_pool": 1000.0,
         "start_date": "2026-09-01", "end_date": "2026-09-10", "keywords": ["subnet"]}
STANDING = {"id": "s1", "kind": "standing", "keywords": ["subnet"]}
NOW = ts(2026, 9, 12, 12, 0, 0)
DAY = 86400
UPLOADED = int(ts(2026, 8, 20, 8, 0, 0))
DRAFT = ("Herald, a Bittensor subnet that pays for verified media coverage, opened its public pilot to "
         "PR firms and PR professionals this week. Contributors upload the text they plan to publish, "
         "then add the link once the article is live. Validators fetch each article, confirm the outlet "
         "and the publication date, and check that the uploaded text appears in the published story "
         "before any reward begins to vest.")
BODY = "Subnet pilot opens to the public\n" + DRAFT
# The draft as an editor might run it: a headline and byline added, a few words changed.
LIGHTLY_EDITED = (
    "Subnet pilot opens to PR firms\nBy a staff reporter\n"
    "Herald, a Bittensor subnet that pays for verified media coverage, opened its public pilot to "
    "PR firms and PR specialists this week. Contributors upload the text they intend to publish, "
    "then add the link once the article is live. Validators fetch each article, confirm the outlet "
    "and the publication date, and check that the uploaded text appears in the published story "
    "before any reward starts to vest.\nMore on the subnet economy next week."
)
# A genuine article on the brief's topic that was not written from the draft.
UNRELATED_ON_TOPIC = (
    "The subnet pilot drew a crowd of early users on Tuesday. Several agencies said they would "
    "test the program with regional newspapers, while one analyst warned that coverage "
    "incentives could shift how press releases are written. Organisers expect a second round "
    "of briefs before the end of the month, and a public dashboard is planned for October."
)


def page(published=ts(2026, 9, 8, 9, 0, 0), text=BODY, article_text=None, status=200, ok=True,
         exact=False, date=None):
    return lambda url: SimpleNamespace(ok=ok, status=status, final_url=url, text_hash="h", text=text,
                                       article_text=article_text, published_ts=published,
                                       published_exact=exact, published_date=date)


def stated(raw):
    """A live page whose publication time is `raw`, read by the page parser."""
    published, exact, date = _parse_published_value(raw)
    return page(published=published, exact=exact, date=date)


def indexed(url):
    return SimpleNamespace(in_index=True, matched_url=url)


def not_indexed(url):
    return SimpleNamespace(in_index=False, matched_url=None)


def must_not_run(*_args):
    raise AssertionError("ran after an earlier check had already failed")


def verify(url=URL, brief=BRIEF, fetch_fn=None, search_fn=indexed, judge_fn=None, now_ts=NOW,
           draft_text=DRAFT, uploaded_ts=UPLOADED):
    return verify_article(url, brief, REGISTRY, fetch_fn or page(), search_fn, judge_fn, now_ts,
                          draft_text=draft_text, uploaded_ts=uploaded_ts)


def test_listed_live_on_topic_article_passes_at_tier_value():
    r = verify()
    assert r.passed and r.reason == "ok"
    assert r.usd == pytest.approx(500.0)
    assert r.article_id == article_id(URL) and r.brief_id == "b1"
    assert r.evidence["outlet_id"] == "guardian" and r.evidence["in_index"] is True


def test_unlisted_outlet_is_rejected_before_fetching():
    r = verify(url="https://contentfarm.example/story", fetch_fn=must_not_run)
    assert not r.passed and r.reason == "outlet_not_listed" and r.usd == 0.0


def test_api_outlet_is_not_supported():
    r = verify(url="https://www.nytimes.com/2026/09/08/technology/subnet.html", fetch_fn=must_not_run)
    assert not r.passed and r.reason == "outlet_not_supported"


def test_disabled_outlet_is_not_supported():
    r = verify(url="https://paused.example.com/story", fetch_fn=must_not_run)
    assert not r.passed and r.reason == "outlet_not_supported"


def test_proxy_outlet_is_supported():
    r = verify(url="https://techcrunch.com/2026/09/08/subnet-pilot")
    assert r.passed and r.usd == pytest.approx(300.0)


def test_page_not_found_is_not_live():
    r = verify(fetch_fn=page(status=404, ok=False), search_fn=must_not_run)
    assert not r.passed and r.reason == "url_not_live"


def test_missing_publication_date_is_unverifiable():
    r = verify(fetch_fn=page(published=None), search_fn=must_not_run)
    assert not r.passed and r.reason == "publication_date_unverifiable"


@pytest.mark.parametrize("published, passes", [
    (ts(2026, 8, 29, 0, 0, 0), True),     # three days before start_date, at 00:00 UTC
    (ts(2026, 8, 28, 23, 59, 59), False),  # four days before start_date
    (ts(2026, 8, 28, 12, 0, 0), False),
    (ts(2026, 9, 10, 23, 59, 59), True),   # end_date 23:59:59 UTC
    (ts(2026, 9, 11, 0, 0, 0), False),     # after end_date 23:59:59 UTC
])
def test_client_brief_publication_window(published, passes):
    r = verify(fetch_fn=page(published=published))
    assert r.passed is passes
    if not passes:
        assert r.reason == "published_outside_window"


def test_article_older_than_the_age_limit_by_chain_time_is_rejected():
    assert verify(brief=STANDING, fetch_fn=page(published=NOW - 21 * DAY)).passed
    r = verify(brief=STANDING, fetch_fn=page(published=NOW - 21 * DAY - 1))
    assert not r.passed and r.reason == "published_outside_window"


def test_age_limit_applies_inside_a_long_brief_window():
    brief = {**BRIEF, "start_date": "2026-08-01", "end_date": "2026-09-30"}
    r = verify(brief=brief, fetch_fn=page(published=ts(2026, 8, 20, 12, 0, 0)))
    assert not r.passed and r.reason == "published_outside_window"


def test_future_dated_article_is_rejected():
    assert verify(brief=STANDING, fetch_fn=page(published=NOW)).passed
    r = verify(brief=STANDING, fetch_fn=page(published=NOW + 1))
    assert not r.passed and r.reason == "published_outside_window"


def test_standing_brief_uses_the_age_rule_only():
    standing = {**STANDING, "start_date": "2026-01-01", "end_date": "2026-01-31"}
    assert verify(brief=standing, fetch_fn=page(published=ts(2026, 9, 1, 0, 0, 0))).passed


def test_brief_without_start_date_has_no_lower_brief_bound():
    brief = {k: v for k, v in BRIEF.items() if k != "start_date"}
    assert verify(brief=brief, fetch_fn=page(published=ts(2026, 8, 25, 0, 0, 0))).passed


def test_disclosed_sponsored_body_is_paid_content():
    r = verify(fetch_fn=page(text="Sponsored content. " + BODY), search_fn=must_not_run)
    assert not r.passed and r.reason == "paid_not_real_news" and r.evidence["paid"] is True


def test_paid_and_topic_checks_read_the_extracted_article_body():
    r = verify(fetch_fn=page(text="Sponsored content directory. Unrelated.", article_text=BODY))
    assert r.passed and r.evidence["draft_match"] == 1.0


def test_keyword_miss_is_a_topic_mismatch():
    r = verify(brief={**BRIEF, "keywords": ["football"]}, search_fn=must_not_run)
    assert not r.passed and r.reason == "topic_mismatch" and r.evidence["topic_match"] is False


def test_tier2_article_missing_from_search_pays_the_search_floor():
    r = verify(url="https://techcrunch.com/2026/09/08/subnet-pilot", search_fn=not_indexed)
    assert r.passed and r.evidence["in_index"] is False
    assert r.usd == pytest.approx(500 * 0.6 * 0.5)


def test_lightly_edited_copy_of_the_draft_passes():
    r = verify(fetch_fn=page(text=LIGHTLY_EDITED))
    assert r.passed and r.reason == "ok"
    assert config.HERALD_DRAFT_MATCH_THRESHOLD <= r.evidence["draft_match"] < 1.0


def test_unrelated_on_topic_article_is_a_draft_mismatch():
    assert topic_matched(UNRELATED_ON_TOPIC, BRIEF)
    r = verify(fetch_fn=page(text=UNRELATED_ON_TOPIC), search_fn=must_not_run)
    assert not r.passed and r.reason == "draft_mismatch" and r.usd == 0.0
    assert r.evidence["draft_match"] < config.HERALD_DRAFT_MATCH_THRESHOLD
    assert "paid" not in r.evidence and "topic_match" not in r.evidence


def test_draft_match_reads_the_extracted_article_body():
    assert verify(fetch_fn=page(text="Site navigation. Unrelated.", article_text=LIGHTLY_EDITED)).passed
    r = verify(fetch_fn=page(text=BODY, article_text=UNRELATED_ON_TOPIC), search_fn=must_not_run)
    assert r.reason == "draft_mismatch"


def test_draft_mismatch_is_decided_before_paid_content():
    r = verify(fetch_fn=page(text="Sponsored content. " + UNRELATED_ON_TOPIC), search_fn=must_not_run)
    assert r.reason == "draft_mismatch"


def test_draft_match_uses_the_configured_threshold(monkeypatch):
    monkeypatch.setattr(oracle, "HERALD_DRAFT_MATCH_THRESHOLD", 1.0)
    assert verify(fetch_fn=page(text=LIGHTLY_EDITED)).reason == "draft_mismatch"
    assert verify().passed


def test_default_draft_match_threshold_is_the_attribution_text_threshold():
    if os.getenv("HERALD_DRAFT_MATCH_THRESHOLD") or os.getenv("HERALD_ATTR_TEXT_THRESHOLD"):
        pytest.skip("a threshold is set in the environment")
    assert config.HERALD_DRAFT_MATCH_THRESHOLD == config.HERALD_ATTR_TEXT_THRESHOLD == 0.6


@pytest.mark.parametrize("uploaded, published", [
    (ts(2026, 9, 8, 23, 59, 59), ts(2026, 9, 8, 0, 0, 0)),  # a date-only publication on the upload day
    (ts(2026, 9, 8, 12, 0, 0), ts(2026, 9, 8, 12, 0, 0)),
    (ts(2026, 9, 7, 23, 59, 59), ts(2026, 9, 8, 0, 0, 0)),
])
def test_publication_on_the_upload_utc_day_or_later_passes(uploaded, published):
    r = verify(fetch_fn=page(published=published), uploaded_ts=int(uploaded))
    assert r.passed and r.reason == "ok"


@pytest.mark.parametrize("uploaded, published", [
    (ts(2026, 9, 8, 0, 0, 0), ts(2026, 9, 7, 23, 59, 59)),
    (ts(2026, 9, 8, 23, 59, 59), ts(2026, 9, 7, 0, 0, 0)),
])
def test_publication_the_utc_day_before_the_upload_is_rejected(uploaded, published):
    r = verify(fetch_fn=page(published=published), uploaded_ts=int(uploaded), search_fn=must_not_run)
    assert not r.passed and r.reason == "published_before_upload" and r.usd == 0.0


@pytest.mark.parametrize("uploaded, published, exact, passes", [
    # An exact publication time is compared with the upload time.
    (ts(2026, 9, 8, 9, 0, 0), ts(2026, 9, 8, 9, 0, 0), True, True),
    (ts(2026, 9, 8, 8, 59, 59), ts(2026, 9, 8, 9, 0, 0), True, True),
    (ts(2026, 9, 7, 23, 0, 0), ts(2026, 9, 8, 9, 0, 0), True, True),
    (ts(2026, 9, 8, 9, 0, 1), ts(2026, 9, 8, 9, 0, 0), True, False),
    (ts(2026, 9, 8, 23, 59, 59), ts(2026, 9, 8, 9, 0, 0), True, False),
    (ts(2026, 9, 9, 0, 0, 0), ts(2026, 9, 8, 9, 0, 0), True, False),
    # Otherwise the UTC days are compared.
    (ts(2026, 9, 8, 9, 0, 1), ts(2026, 9, 8, 9, 0, 0), False, True),
    (ts(2026, 9, 8, 23, 59, 59), ts(2026, 9, 8, 0, 0, 0), False, True),
    (ts(2026, 9, 9, 0, 0, 0), ts(2026, 9, 8, 23, 59, 59), False, False),
])
def test_published_after_upload_uses_the_time_when_exact_and_the_day_otherwise(uploaded, published,
                                                                              exact, passes):
    assert published_after_upload(published, int(uploaded), exact=exact) is passes
    if not exact:
        assert published_after_upload(published, int(uploaded)) is passes  # the default


def test_same_day_upload_after_an_exact_publication_time_is_rejected():
    r = verify(fetch_fn=page(published=ts(2026, 9, 8, 9, 0, 0), exact=True),
               uploaded_ts=int(ts(2026, 9, 8, 15, 0, 0)), search_fn=must_not_run)
    assert not r.passed and r.reason == "published_before_upload" and r.usd == 0.0
    assert r.evidence["published_exact"] is True and "draft_match" not in r.evidence


def test_same_day_upload_passes_the_day_rule_when_the_publication_time_is_not_exact():
    r = verify(fetch_fn=page(published=ts(2026, 9, 8, 9, 0, 0), exact=False),
               uploaded_ts=int(ts(2026, 9, 8, 15, 0, 0)))
    assert r.passed and r.reason == "ok"
    assert r.evidence["published_exact"] is False


def test_upload_before_an_exact_publication_time_passes():
    r = verify(fetch_fn=page(published=ts(2026, 9, 8, 9, 0, 0), exact=True),
               uploaded_ts=int(ts(2026, 9, 8, 8, 59, 59)))
    assert r.passed and r.evidence["published_exact"] is True
    assert r.evidence["published_ts"] == ts(2026, 9, 8, 9, 0, 0)


@pytest.mark.parametrize("raw, passes", [
    # A midnight placeholder for a date: the day rule accepts an upload later that day.
    ("2026-09-08T00:00:00Z", True),
    ("2026-09-08T00:00:00.000Z", True),
    ("2026-09-08T00:00:00-04:00", True),
    # A real morning time: an upload later the same day is rejected.
    ("2026-09-08T07:30:00Z", False),
    ("2026-09-08T03:30:00-04:00", False),
])
def test_same_day_upload_after_a_midnight_placeholder_passes_and_after_a_real_time_does_not(raw,
                                                                                           passes):
    r = verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 8, 8, 0, 0)))
    assert r.passed is passes
    assert r.evidence["published_exact"] is not passes
    if not passes:
        assert r.reason == "published_before_upload"


AT_08_UTC = int(ts(2026, 9, 8, 8, 0, 0))
ENDS_09_07 = {**BRIEF, "start_date": "2026-09-01", "end_date": "2026-09-07"}
STARTS_09_08 = {**BRIEF, "start_date": "2026-09-08", "end_date": "2026-09-10"}


@pytest.fixture
def no_buffer(monkeypatch):
    """Publication windows open on the brief's start date itself."""
    monkeypatch.setattr(oracle, "HERALD_PUBLISH_BUFFER_DAYS", 0)


def test_positive_offset_midnight_is_judged_by_the_date_the_page_states(no_buffer):
    raw = "2026-09-08T00:00:00+02:00"  # 22:00 UTC on 7 September
    r = verify(fetch_fn=stated(raw), uploaded_ts=AT_08_UTC)
    assert r.passed and r.evidence["published_date"] == "2026-09-08"
    assert r.evidence["published_exact"] is False
    # The window reads the same day: after a window ending 7 September, inside one opening on the 8th.
    r = verify(brief=ENDS_09_07, fetch_fn=stated(raw), uploaded_ts=UPLOADED)
    assert r.reason == "published_outside_window"
    assert verify(brief=STARTS_09_08, fetch_fn=stated(raw), uploaded_ts=AT_08_UTC).passed
    # Without the stated date, the UTC day (7 September) would have decided both checks.
    utc_only = page(published=ts(2026, 9, 7, 22, 0, 0))
    assert verify(fetch_fn=utc_only, uploaded_ts=AT_08_UTC).reason == "published_before_upload"
    assert verify(brief=STARTS_09_08, fetch_fn=utc_only).reason == "published_outside_window"
    assert verify(brief=ENDS_09_07, fetch_fn=utc_only).passed


@pytest.mark.parametrize("raw", ["2026-09-08T00:00:00-04:00", "2026-09-08T00:00:00Z"])
def test_negative_offset_and_utc_midnights_keep_their_day(no_buffer, raw):
    assert verify(fetch_fn=stated(raw), uploaded_ts=AT_08_UTC).passed
    r = verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 9, 0, 0, 0)))
    assert r.reason == "published_before_upload"
    assert verify(brief=ENDS_09_07, fetch_fn=stated(raw)).reason == "published_outside_window"
    assert verify(brief=STARTS_09_08, fetch_fn=stated(raw)).passed


@pytest.mark.parametrize("raw", ["2026-09-08", "2026/09/08", "2026-09-08T23:30:00"])
def test_a_date_or_a_time_without_offset_is_judged_by_the_date_it_names(no_buffer, raw):
    assert verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 8, 23, 59, 59))).passed
    r = verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 9, 0, 0, 0)))
    assert r.reason == "published_before_upload" and r.evidence["published_date"] == "2026-09-08"
    assert verify(brief=ENDS_09_07, fetch_fn=stated(raw)).reason == "published_outside_window"
    assert verify(brief={**STARTS_09_08, "end_date": "2026-09-08"}, fetch_fn=stated(raw)).passed


def test_exact_times_still_compare_instants(no_buffer):
    raw = "2026-09-08T00:30:00+02:00"  # exact: 22:30 UTC on 7 September
    r = verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 7, 22, 0, 0)))
    assert r.passed and r.evidence["published_exact"] is True and r.evidence["published_date"] is None
    r = verify(fetch_fn=stated(raw), uploaded_ts=int(ts(2026, 9, 7, 22, 30, 1)))
    assert r.reason == "published_before_upload"
    # The window places it on its UTC day, 7 September.
    assert verify(brief=ENDS_09_07, fetch_fn=stated(raw)).passed
    assert verify(brief=STARTS_09_08, fetch_fn=stated(raw)).reason == "published_outside_window"


def test_a_stated_date_is_ignored_for_an_exact_time():
    r = verify(fetch_fn=page(published=ts(2026, 9, 8, 9, 0, 0), exact=True, date="2026-09-09"),
               uploaded_ts=int(ts(2026, 9, 8, 15, 0, 0)))
    assert r.reason == "published_before_upload" and r.evidence["published_date"] is None


def test_page_without_an_exactness_flag_uses_the_day_rule():
    def dated(url):
        return SimpleNamespace(ok=True, status=200, final_url=url, text_hash="h", text=BODY,
                               article_text=None, published_ts=ts(2026, 9, 8, 9, 0, 0))

    r = verify(fetch_fn=dated, uploaded_ts=int(ts(2026, 9, 8, 15, 0, 0)))
    assert r.passed and r.evidence["published_exact"] is False


def test_publication_window_is_checked_before_the_upload_day():
    r = verify(fetch_fn=page(published=ts(2026, 8, 28, 12, 0, 0)), uploaded_ts=int(ts(2026, 9, 1, 0, 0, 0)))
    assert r.reason == "published_outside_window"


def test_upload_day_is_checked_before_the_draft_match():
    r = verify(fetch_fn=page(published=ts(2026, 9, 7, 12, 0, 0), text=UNRELATED_ON_TOPIC),
               uploaded_ts=int(ts(2026, 9, 8, 12, 0, 0)), search_fn=must_not_run)
    assert r.reason == "published_before_upload" and "draft_match" not in r.evidence


@pytest.mark.parametrize("text", [BODY, UNRELATED_ON_TOPIC])
def test_evidence_never_carries_the_draft(text):
    r = verify(fetch_fn=page(text=text))
    recorded = repr(r.evidence).lower()
    assert "contributors upload the text" not in recorded and "bittensor" not in recorded
