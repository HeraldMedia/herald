from herald.validator.news import search as searchmod
from herald.validator.news.search import in_index


def _stub(monkeypatch, links):
    monkeypatch.setattr(searchmod, "SERPAPI_API_KEY", "k")  # provider is now key-gated
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda query, num: links)


def test_matches_article_url(monkeypatch):
    _stub(monkeypatch, ["https://www.nytimes.com/a?utm_source=x", "https://o.com/b"])
    r = in_index("https://www.nytimes.com/a")
    assert r.in_index and r.matched_url == "https://www.nytimes.com/a"


def test_no_match_when_absent(monkeypatch):
    _stub(monkeypatch, ["https://o.com/b", "https://p.com/c"])
    assert in_index("https://www.nytimes.com/a").in_index is False


def test_network_error_is_not_indexed(monkeypatch):
    def boom(query, num):
        raise RuntimeError("rate limit")
    monkeypatch.setattr(searchmod, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(searchmod, "_serpapi_search", boom)
    assert in_index("https://www.nytimes.com/a").in_index is False


def test_brave_can_be_the_only_provider(monkeypatch):
    # Use Brave INSTEAD of SerpAPI: only BRAVE_API_KEY set -> Brave is the sole search provider.
    monkeypatch.setattr(searchmod, "SERPAPI_API_KEY", None)
    monkeypatch.setattr(searchmod, "BRAVE_API_KEY", "b")
    monkeypatch.setattr(searchmod, "_brave_search", lambda q, n: ["https://www.nytimes.com/a"])
    assert in_index("https://www.nytimes.com/a").in_index is True


def test_no_search_provider_is_not_indexed(monkeypatch):
    # Neither key set -> can't confirm indexing -> not indexed (earns the floor, never full credit).
    monkeypatch.setattr(searchmod, "SERPAPI_API_KEY", None)
    monkeypatch.setattr(searchmod, "BRAVE_API_KEY", None)
    assert in_index("https://www.nytimes.com/a").in_index is False


def test_malformed_provider_response_does_not_crash(monkeypatch):
    # A non-list, or a list with non-string links, must not raise out of in_index — that
    # would abort the whole epoch's scoring. Junk is skipped; a real match still counts.
    monkeypatch.setattr(searchmod, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda q, n: None)
    assert in_index("https://www.nytimes.com/a").in_index is False
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda q, n: [123, None, {"x": 1}])
    assert in_index("https://www.nytimes.com/a").in_index is False
    monkeypatch.setattr(searchmod, "_serpapi_search", lambda q, n: [123, "https://www.nytimes.com/a", None])
    assert in_index("https://www.nytimes.com/a").in_index is True
