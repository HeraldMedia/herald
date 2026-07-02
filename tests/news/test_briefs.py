from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from herald.validator.utils import briefs as briefsmod


class FakeCache(dict):
    def set(self, k, v):
        self[k] = v

    def get(self, k, default=None):
        return dict.get(self, k, default)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(briefsmod.BriefsCache, "get_cache", classmethod(lambda cls: fake))
    monkeypatch.setattr(briefsmod, "VEST_EPOCHS", 30)
    monkeypatch.delenv("HERALD_BRIEFS_PUBKEY", raising=False)
    monkeypatch.delenv("HERALD_REQUIRE_SIGNED_BRIEFS", raising=False)
    return fake


def _serve(monkeypatch, items):
    def fake_get(url, timeout=None):
        assert timeout is not None  # the no-timeout hang bug must not come back
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": items})

    monkeypatch.setattr(briefsmod.requests, "get", fake_get)


def test_standing_brief_always_active(monkeypatch):
    # kind == "standing" or a missing end_date -> always served (the always-open brief must not
    # need a far-future end_date to survive the window filter)
    _serve(monkeypatch, [{"id": "s1", "kind": "standing"}, {"id": "n1"}])
    got = briefsmod.get_briefs(now=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert [b["id"] for b in got] == ["s1", "n1"]


def test_client_brief_window_includes_persistence_tail(monkeypatch):
    brief = {"id": "c1", "kind": "client", "start_date": "2026-06-01", "end_date": "2026-06-20"}
    _serve(monkeypatch, [brief])

    def active_on(y, m, d):
        return briefsmod.get_briefs(now=datetime(y, m, d, tzinfo=timezone.utc))

    assert active_on(2026, 5, 31) == []        # not started
    assert active_on(2026, 6, 1) == [brief]    # scorable from day one (no reward-delay offset)
    assert active_on(2026, 6, 20) == [brief]   # last open day
    assert active_on(2026, 7, 10) == [brief]   # closed, but inside the 30-day persistence tail
    assert active_on(2026, 7, 20) == [brief]   # tail's last day
    assert active_on(2026, 7, 21) == []        # tail over


def test_invalid_dates_dropped_not_fatal(monkeypatch):
    _serve(monkeypatch, [{"id": "bad", "end_date": "junk"}, {"id": "s1", "kind": "standing"}])
    got = briefsmod.get_briefs(now=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert [b["id"] for b in got] == ["s1"]


def test_all_flag_skips_window_filter(monkeypatch):
    _serve(monkeypatch, [{"id": "c1", "start_date": "2020-01-01", "end_date": "2020-01-02"}])
    assert briefsmod.get_briefs(all=True) == [{"id": "c1", "start_date": "2020-01-01", "end_date": "2020-01-02"}]


def test_api_error_falls_back_to_cache(monkeypatch):
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _serve(monkeypatch, [{"id": "s1", "kind": "standing"}])
    assert briefsmod.get_briefs(now=now)[0]["id"] == "s1"  # primes the cache

    def boom(url, timeout=None):
        raise requests.exceptions.ConnectTimeout("board unreachable")

    monkeypatch.setattr(briefsmod.requests, "get", boom)
    assert briefsmod.get_briefs(now=now)[0]["id"] == "s1"  # served from cache
