from herald.validator.news import fetch as fetchmod
from herald.validator.news import search as searchmod
from herald.validator.news.fetch import fetch
from herald.validator.news.search import in_index


# ── fetch quorum ──────────────────────────────────────────────────────────────

def test_fetch_quorum_two_agree(monkeypatch):
    p = lambda u: (200, u, b"x" * 1000)
    monkeypatch.setattr(fetchmod, "_providers", lambda: [p, p])
    monkeypatch.setattr(fetchmod, "HERALD_QUORUM_THRESHOLD", 2)
    r = fetch("https://x/a")
    assert r.ok and r.providers_live == 2


def test_fetch_quorum_not_met(monkeypatch):
    live = lambda u: (200, u, b"x" * 1000)
    dead = lambda u: (404, u, b"")
    monkeypatch.setattr(fetchmod, "_providers", lambda: [live, dead])
    monkeypatch.setattr(fetchmod, "HERALD_QUORUM_THRESHOLD", 2)
    assert fetch("https://x/a").ok is False  # only 1 of 2 live, need 2


def test_fetch_quorum_threshold_one_tolerates_one_failure(monkeypatch):
    live = lambda u: (200, u, b"x" * 1000)
    dead = lambda u: (404, u, b"")
    monkeypatch.setattr(fetchmod, "_providers", lambda: [live, dead])
    monkeypatch.setattr(fetchmod, "HERALD_QUORUM_THRESHOLD", 1)
    assert fetch("https://x/a").ok is True


def test_fetch_epoch_cache(monkeypatch):
    calls = []

    def p(u):
        calls.append(u)
        return (200, u, b"x" * 1000)

    monkeypatch.setattr(fetchmod, "_providers", lambda: [p])
    fetch("https://x/a", epoch=5)
    fetch("https://x/a", epoch=5)
    assert len(calls) == 1            # second call served from cache
    fetch("https://x/a", epoch=6)
    assert len(calls) == 2            # new epoch re-fetches


# ── search quorum ─────────────────────────────────────────────────────────────

def test_search_quorum_two_agree(monkeypatch):
    p = lambda q, n: ["https://x/a"]
    monkeypatch.setattr(searchmod, "_providers", lambda: [p, p])
    monkeypatch.setattr(searchmod, "HERALD_QUORUM_THRESHOLD", 2)
    assert in_index("https://x/a").in_index is True


def test_search_quorum_not_met(monkeypatch):
    has = lambda q, n: ["https://x/a"]
    missing = lambda q, n: ["https://other/b"]
    monkeypatch.setattr(searchmod, "_providers", lambda: [has, missing])
    monkeypatch.setattr(searchmod, "HERALD_QUORUM_THRESHOLD", 2)
    assert in_index("https://x/a").in_index is False


def test_search_epoch_cache(monkeypatch):
    calls = []

    def p(q, n):
        calls.append(q)
        return ["https://x/a"]

    monkeypatch.setattr(searchmod, "_providers", lambda: [p])
    in_index("https://x/a", epoch=3)
    in_index("https://x/a", epoch=3)
    assert len(calls) == 1


def test_fetch_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(fetchmod, "_CACHE_MAX", 3)
    monkeypatch.setattr(fetchmod, "_providers", lambda: [lambda u: (200, u, b"x" * 1000)])
    for i in range(10):
        fetch(f"https://x/{i}", epoch=1)
    assert len(fetchmod._cache) <= 3
