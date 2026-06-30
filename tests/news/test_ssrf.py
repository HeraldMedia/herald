from herald.validator.news import fetch as fetchmod
from herald.validator.news.fetch import fetch, is_safe_fetch_url


def test_blocks_non_http_schemes():
    assert is_safe_fetch_url("file:///etc/passwd") is False
    assert is_safe_fetch_url("ftp://example.com/x") is False
    assert is_safe_fetch_url("gopher://example.com") is False


def test_blocks_private_and_metadata_ips():
    assert is_safe_fetch_url("http://169.254.169.254/latest/meta-data") is False  # link-local
    assert is_safe_fetch_url("http://127.0.0.1/") is False
    assert is_safe_fetch_url("http://10.0.0.1/") is False
    assert is_safe_fetch_url("http://192.168.1.1/") is False


def test_allows_public_hosts():
    assert is_safe_fetch_url("https://www.nytimes.com/a") is True
    assert is_safe_fetch_url("http://8.8.8.8/") is True


def test_fetch_refuses_unsafe_url_without_calling_provider(monkeypatch):
    called = []
    monkeypatch.setattr(fetchmod, "_http_get", lambda u: called.append(u) or (200, u, b"x" * 1000))
    r = fetch("http://169.254.169.254/latest/meta-data")
    assert r.ok is False and called == []
