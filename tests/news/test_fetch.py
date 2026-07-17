from types import SimpleNamespace

import pytest

from herald.validator.news import fetch as fetchmod
from herald.validator.news.fetch import FetchResult, fetch, fetch_article
from herald.validator.news.registry import OutletRegistry


def test_allow_local_fetch_flag(monkeypatch):
    # Default: literal loopback/private is SSRF-blocked. (conftest's _stub_dns keeps hostnames
    # resolving public, so we assert against literal IPs here.) HERALD_ALLOW_LOCAL_FETCH allows them
    # for a localhost sim ONLY (never set in prod); the http(s) scheme gate still applies.
    monkeypatch.setattr(fetchmod, "HERALD_ALLOW_LOCAL_FETCH", False)
    assert fetchmod.is_safe_fetch_url("http://127.0.0.1:9100/x") is False
    assert fetchmod.is_safe_fetch_url("http://10.0.0.1/x") is False
    monkeypatch.setattr(fetchmod, "HERALD_ALLOW_LOCAL_FETCH", True)
    assert fetchmod.is_safe_fetch_url("http://127.0.0.1:9100/x") is True
    assert fetchmod.is_safe_fetch_url("http://localhost:9100/nytimes/x") is True
    assert fetchmod.is_safe_fetch_url("ftp://localhost/x") is False  # scheme still enforced


def test_scrapingbee_base_is_configurable(monkeypatch):
    captured = {}
    monkeypatch.setattr(fetchmod, "HERALD_SCRAPINGBEE_BASE", "http://localhost:9100/scrapingbee/api/v1")
    monkeypatch.setattr(fetchmod, "SCRAPINGBEE_API_KEY", "sim")

    def fake_get(base, params=None, headers=None, timeout=None):
        captured.update(base=base, params=params, headers=headers)
        return SimpleNamespace(status_code=200, content=b"x" * 600)

    monkeypatch.setattr(fetchmod.httpx, "get", fake_get)
    status, _, _ = fetchmod._scrapingbee_get("http://localhost:9100/reuters/slug")
    assert status == 200
    assert captured["base"] == "http://localhost:9100/scrapingbee/api/v1"
    assert captured["params"]["url"] == "http://localhost:9100/reuters/slug"
    assert "api_key" not in captured["params"]
    assert captured["headers"] == {"Authorization": "Bearer sim"}


@pytest.mark.parametrize(("profile", "expected"), [
    ("classic", {"render_js": "false"}),
    ("js", {"render_js": "true"}),
    ("premium", {"render_js": "false", "premium_proxy": "true"}),
    ("premium_js", {"render_js": "true", "premium_proxy": "true"}),
    ("stealth", {"stealth_proxy": "true"}),
])
def test_scrapingbee_fetch_profiles(monkeypatch, profile, expected):
    captured = {}
    monkeypatch.setattr(fetchmod, "SCRAPINGBEE_API_KEY", "sim")

    def fake_get(_base, params=None, headers=None, timeout=None):
        captured.update(params=params, headers=headers)
        return SimpleNamespace(status_code=200, content=b"x" * 600)

    monkeypatch.setattr(fetchmod.httpx, "get", fake_get)
    fetchmod._scrapingbee_get("https://example.com/story", profile=profile)
    assert captured["params"] == {"url": "https://example.com/story", **expected}
    assert captured["headers"] == {"Authorization": "Bearer sim"}


def _stub(monkeypatch, status, body):
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (status, url, body))


_REG = OutletRegistry.from_dict({"version_id": 1, "outlets": [
    {"outlet_id": "guardian", "tier": 1, "domains": ["theguardian.com"]},                    # direct
    {"outlet_id": "reuters", "tier": 1, "domains": ["reuters.com"], "fetch": "proxy"},
    {"outlet_id": "marketwatch", "tier": 1, "domains": ["marketwatch.com"], "fetch": "proxy:premium"},
    {"outlet_id": "disabled", "tier": 1, "domains": ["disabled.example"], "fetch": "disabled"},
    {"outlet_id": "nytimes", "tier": 1, "domains": ["nytimes.com"], "fetch": "api:nyt"},
]})


def test_fetch_article_direct_uses_http(monkeypatch):
    fetchmod._cache.clear()
    monkeypatch.setattr(fetchmod, "is_safe_fetch_url", lambda u: True)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    fr = fetch_article("https://theguardian.com/a", _REG)
    assert fr.ok and fr.body_kind == "full"


def test_fetch_article_proxy_without_provider_fails_closed(monkeypatch):
    fetchmod._cache.clear()
    monkeypatch.setattr(fetchmod, "is_safe_fetch_url", lambda u: True)
    monkeypatch.setattr(fetchmod, "SCRAPINGBEE_API_KEY", None)
    # proxy outlet with no proxy provider configured -> can't verify -> not live (fail closed),
    # and the plain-HTTP provider must NOT be used to "rescue" a bot-walled outlet.
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, b"news " * 200))
    assert fetch_article("https://reuters.com/a", _REG).ok is False


def test_fetch_article_passes_signed_proxy_profile(monkeypatch):
    fetchmod._cache.clear()
    captured = {}
    monkeypatch.setattr(fetchmod, "SCRAPINGBEE_API_KEY", "sim")

    def fake_proxy(url, profile="classic"):
        captured.update(url=url, profile=profile)
        return 200, url, b"news " * 200

    monkeypatch.setattr(fetchmod, "_scrapingbee_get", fake_proxy)
    fr = fetch_article("https://marketwatch.com/story/a", _REG)
    assert fr.ok is True
    assert captured == {"url": "https://marketwatch.com/story/a", "profile": "premium"}


def test_fetch_article_disabled_strategy_fails_closed(monkeypatch):
    fetchmod._cache.clear()
    monkeypatch.setattr(fetchmod, "_http_get", lambda _url: (_ for _ in ()).throw(AssertionError))
    assert fetch_article("https://disabled.example/a", _REG).ok is False


def test_fetch_article_api_delegates_to_adapter(monkeypatch):
    from herald.validator.news import adapters
    monkeypatch.setattr(adapters, "api_fetch",
                        lambda name, url, epoch=None: FetchResult(True, 200, url, "", 5,
                                                                  text="lead", body_kind="excerpt"))
    fr = fetch_article("https://nytimes.com/a", _REG)
    assert fr.ok and fr.body_kind == "excerpt" and fr.text == "lead"


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


def test_extracts_article_scoped_text_without_footer_disclosures(monkeypatch):
    html = (b"<html><body><article><h1>Markets update</h1><p>" + b"Editorial reporting. " * 40 + b"</p></article>"
            b"<footer>Sponsored content directory</footer></body></html>" + b"x" * 600)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    r = fetch("https://x/article")
    assert "Editorial reporting" in r.article_text
    assert "Sponsored content directory" not in r.article_text
    assert "Sponsored content directory" in r.text


def test_article_scope_prefers_content_container_over_broad_main(monkeypatch):
    html = (b"<html><body><main><nav>Sponsored Featured Resources</nav>"
            b'<div class="article-contents"><h1>Security report</h1><p>'
            + b"Independent editorial reporting. " * 40
            + b"</p></div><aside>Press releases</aside></main></body></html>")
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    r = fetch("https://x/article-body")
    assert "Independent editorial reporting" in r.article_text
    assert "Sponsored Featured Resources" not in r.article_text
    assert "Press releases" not in r.article_text


def test_tiny_article_wrapper_does_not_eclipse_real_article_body(monkeypatch):
    html = (b'<html><body><article>Share</article><div itemprop="articleBody text">'
            + b"Substantial independent reporting. " * 40
            + b"</div></body></html>")
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    r = fetch("https://x/tiny-article-wrapper")
    assert "Substantial independent reporting" in r.article_text
    assert "Share" not in r.article_text


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


def test_published_ts_tolerates_trailing_punctuation_and_cxense_meta(monkeypatch):
    from datetime import datetime, timezone
    expect = datetime(2026, 7, 1, 17, 32, 48, tzinfo=timezone.utc).timestamp()
    jsonld = b'<script>{"datePublished":"2026-07-01T13:32:48-0400."}</script>' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, jsonld))
    assert fetch("https://x/jsonld").published_ts == expect

    fetchmod._cache.clear()
    meta = b'<meta name="cXenseParse:recs:publishtime" content="2026-07-01T17:32:48">' + b"x" * 600
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, meta))
    assert fetch("https://x/cxense").published_ts == expect


def test_published_ts_parses_lancet_citation_online_date(monkeypatch):
    from datetime import datetime, timezone
    html = (b'<meta name="citation_date" content="2026/07/04">'
            b'<meta name="citation_online_date" content="2026/06/17">' + b"x" * 600)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    expect = datetime(2026, 6, 17, tzinfo=timezone.utc).timestamp()
    assert fetch("https://x/lancet").published_ts == expect


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


def test_parses_author_from_jsonld_array(monkeypatch):
    # The Guardian (and many outlets) publish authors as an ARRAY: "author":[{…"name":"…"}].
    # The article:author meta alongside it is only a profile URL, which the parser skips.
    fetchmod._cache.clear()
    html = (b'<meta property="article:author" content="https://www.theguardian.com/profile/shaun-walker"/>'
            b'<script>{"author":[{"@type":"Person","name":"Shaun Walker"}],"datePublished":"2026-07-02"}</script>'
            + b"x" * 600)
    monkeypatch.setattr(fetchmod, "_http_get", lambda url: (200, url, html))
    assert fetch("https://x/a").author == "Shaun Walker"


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


def test_provider_body_selection_prefers_dated_page(monkeypatch):
    # The direct fetch hits a cookie wall (no metas); the second provider sees the real page.
    # Selection must deterministically prefer the date-bearing body.
    fetchmod._cache.clear()
    walled = b"Accept cookies to continue " * 40
    real = b'<script>{"datePublished":"2026-01-05T00:00:00+00:00"}</script>' + b"Real article body " * 40
    monkeypatch.setattr(fetchmod, "_providers",
                        lambda proxy_only=False, proxy_profile="classic":
                        [lambda u: (200, u, walled), lambda u: (200, u, real)])
    r = fetch("https://x/a")
    assert r.published_ts is not None and "Real article body" in r.text
