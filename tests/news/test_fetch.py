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
