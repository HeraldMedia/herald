from types import SimpleNamespace

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
