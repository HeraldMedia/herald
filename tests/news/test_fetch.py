from herald.validator.news import fetch as fetchmod
from herald.validator.news.fetch import fetch


def _stub(monkeypatch, status, body):
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (status, url, body))


def test_live_200_with_body(monkeypatch):
    _stub(monkeypatch, 200, b"x" * 1000)
    r = fetch("https://nytimes.com/a")
    assert r.ok and r.status == 200 and r.body_len == 1000 and len(r.text_hash) == 64


def test_404_not_live(monkeypatch):
    _stub(monkeypatch, 404, b"x" * 1000)
    assert fetch("https://nytimes.com/a").ok is False


def test_short_body_not_live(monkeypatch):
    _stub(monkeypatch, 200, b"x" * 10)
    assert fetch("https://nytimes.com/a").ok is False


def test_network_error_not_live(monkeypatch):
    def boom(url):
        raise RuntimeError("dns")
    monkeypatch.setattr(fetchmod, "_http_get", boom)
    r = fetch("https://nytimes.com/a")
    assert r.ok is False and r.status == 0


def test_extracts_visible_text_skips_scripts(monkeypatch):
    html = b"<html><head><style>.x{}</style></head><body><h1>Hello</h1>" \
           b"<script>var a=1;</script><p>World news here.</p></body></html>"
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html + b"x" * 500))
    r = fetch("https://nytimes.com/a")
    assert "Hello" in r.text and "World news here." in r.text
    assert "var a=1" not in r.text


def test_parses_published_ts(monkeypatch):
    from datetime import datetime, timezone
    html = b'<script>{"datePublished":"2026-01-05T00:00:00+00:00"}</script>' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    r = fetch("https://x/a")
    assert r.published_ts == datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()


def test_no_published_ts_is_none(monkeypatch):
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"x" * 1000))
    assert fetch("https://x/a").published_ts is None


def test_naive_published_date_is_utc(monkeypatch):
    from datetime import datetime, timezone
    html = b'<script>{"datePublished":"2020-01-01T00:00:00"}</script>' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    # naive date must be interpreted as UTC, identically on every validator (no local-TZ drift)
    assert fetch("https://x/a").published_ts == datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()


def test_published_meta_tag_either_attribute_order(monkeypatch):
    from datetime import datetime, timezone
    expect = datetime(2026, 2, 2, tzinfo=timezone.utc).timestamp()
    a = b'<meta property="article:published_time" content="2026-02-02T00:00:00Z">' + b"x" * 600
    b = b'<meta content="2026-02-02T00:00:00Z" property="article:published_time">' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, a))
    assert fetch("https://x/a").published_ts == expect
    fetchmod._cache.clear()
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b))
    assert fetch("https://x/b").published_ts == expect


def test_published_ts_no_redos_on_pathological_body():
    import time
    # Many content="..." attrs with no '>' and no keyword: the reversed meta pattern must
    # not backtrack quadratically over the body (a crafted page would otherwise pin the
    # validator's eval thread for the whole epoch).
    body = (b'content="x" ' * 40000).decode("utf-8")  # ~480 KB
    start = time.perf_counter()
    ts = fetchmod._parse_published_ts(body)
    assert ts is None
    assert time.perf_counter() - start < 2.0


def test_parses_author_from_jsonld(monkeypatch):
    fetchmod._cache.clear()
    html = b'<script>{"author":{"@type":"Person","name":"Jane Doe"},"datePublished":"2026-01-05"}</script>' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    assert fetch("https://x/a").author == "Jane Doe"


def test_parses_author_meta_either_attribute_order(monkeypatch):
    fetchmod._cache.clear()
    html = b'<meta name="author" content="John Smith">' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    assert fetch("https://x/a").author == "John Smith"

    fetchmod._cache.clear()
    html = b'<meta content="John Smith" name="author">' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    assert fetch("https://x/b").author == "John Smith"


def test_no_author_is_none_and_url_authors_skipped(monkeypatch):
    fetchmod._cache.clear()
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"x" * 1000))
    assert fetch("https://x/a").author is None

    fetchmod._cache.clear()
    html = b'<meta property="article:author" content="https://x.com/profile/jane">' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    assert fetch("https://x/b").author is None
