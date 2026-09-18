from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from herald.validator.news import adapters

DOC = {
    "web_url": "https://www.nytimes.com/2026/07/02/world/x.html",
    "headline": {"main": "Big News Today"},
    "abstract": "A short abstract of the story.",
    "lead_paragraph": "The lead paragraph carries a distinctive verbatim sentence about the event.",
    "section_name": "World",
    "keywords": [{"value": "Ukraine"}, {"value": "Russia"}],
    "byline": {"original": "By Jane Doe and John Roe"},
    "pub_date": "2026-07-02T06:00:00+0000",
}


def _resp(docs):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"response": {"docs": docs}})


def test_nyt_adapter_builds_authoritative_excerpt(monkeypatch):
    monkeypatch.setenv("HERALD_NYT_API_KEY", "k")
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _resp([DOC]))
    fr = adapters.api_fetch("nyt", DOC["web_url"])
    assert fr.ok and fr.body_kind == "excerpt"
    assert fr.text == DOC["lead_paragraph"]              # anchor target = lead paragraph
    assert fr.author == "Jane Doe and John Roe"          # "By " prefix stripped
    assert fr.published_ts is not None
    # topic_text is the unfakeable authoritative blob (headline + abstract + lead + tags + section)
    assert "Big News Today" in fr.topic_text and "Ukraine" in fr.topic_text and "World" in fr.topic_text


@pytest.mark.parametrize("pub_date, published, exact", [
    ("2026-07-02T06:00:00+0000", datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc), True),
    ("2026-07-02T06:00:00Z", datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc), True),
    ("2026-07-02T08:00:00+02:00", datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc), True),
    ("2026-07-02T06:00:00", datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc), False),
    ("2026-07-02", datetime(2026, 7, 2, tzinfo=timezone.utc), False),
])
def test_nyt_publication_time_is_exact_only_with_an_explicit_offset(pub_date, published, exact):
    fr = adapters._from_nyt_doc(DOC["web_url"], {**DOC, "pub_date": pub_date})
    assert fr.published_ts == published.timestamp()
    assert fr.published_exact is exact


@pytest.mark.parametrize("pub_date", [None, "", "not a date", 1751436000])
def test_nyt_missing_or_unreadable_publication_time_is_none(pub_date):
    fr = adapters._from_nyt_doc(DOC["web_url"], {**DOC, "pub_date": pub_date})
    assert fr.published_ts is None and fr.published_exact is False


def test_nyt_adapter_requires_exact_web_url_match(monkeypatch):
    # The slug query can return several docs; only an exact web_url match is accepted, so a miner
    # can't point at URL-A and have a different article verify.
    monkeypatch.setenv("HERALD_NYT_API_KEY", "k")
    other = {**DOC, "web_url": "https://www.nytimes.com/2026/07/02/world/some-other-story.html"}
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _resp([other]))
    fr = adapters.api_fetch("nyt", DOC["web_url"])
    assert fr.ok is False and fr.status == 404


def test_nyt_adapter_no_key_fails_closed(monkeypatch):
    monkeypatch.delenv("HERALD_NYT_API_KEY", raising=False)
    assert adapters.api_fetch("nyt", DOC["web_url"]).ok is False


def test_nyt_adapter_article_not_in_index_is_dead(monkeypatch):
    monkeypatch.setenv("HERALD_NYT_API_KEY", "k")
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _resp([]))
    fr = adapters.api_fetch("nyt", DOC["web_url"])
    assert fr.ok is False and fr.status == 404


def test_nyt_adapter_api_error_fails_closed(monkeypatch):
    monkeypatch.setenv("HERALD_NYT_API_KEY", "k")

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(adapters.httpx, "get", boom)
    assert adapters.api_fetch("nyt", DOC["web_url"]).ok is False


def test_unknown_adapter_fails_closed():
    assert adapters.api_fetch("bogus", "https://x/y").ok is False
