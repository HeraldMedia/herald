import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("HERALD_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("HERALD_CLAIM_STORE", raising=False)
