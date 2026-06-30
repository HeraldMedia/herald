import pytest


@pytest.fixture(autouse=True)
def _clear_news_caches():
    from herald.validator.news import fetch as fetchmod
    from herald.validator.news import search as searchmod
    fetchmod._cache.clear()
    searchmod._cache.clear()
    yield


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    # test hostnames resolve to a public IP by default; SSRF tests override this
    from herald.validator.news import fetch as fetchmod
    monkeypatch.setattr(fetchmod, "_resolve_ips", lambda host: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("HERALD_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("HERALD_CLAIM_STORE", raising=False)
    monkeypatch.delenv("HERALD_RESULTS_ENDPOINT", raising=False)
    monkeypatch.delenv("HERALD_REGISTRY_PUBKEY", raising=False)
    monkeypatch.delenv("HERALD_REGISTRY_AUTHORITY_HOTKEY", raising=False)
