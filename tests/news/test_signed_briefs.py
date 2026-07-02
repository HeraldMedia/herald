import time

import pytest

from herald.validator.news.registry_signing import generate_keypair
from herald.validator.news.signed_briefs import sign_briefs, verify_briefs


def test_sign_verify_round_trip(monkeypatch):
    priv, pub = generate_keypair()
    payload = {"items": [{"id": "0142", "reward_pool": 5000.0}, {"id": "0143", "kind": "standing"}],
               "signed_at": int(time.time())}
    signed = sign_briefs(payload, priv)
    monkeypatch.setenv("HERALD_BRIEFS_PUBKEY", pub)
    assert verify_briefs(signed)["items"][0]["reward_pool"] == 5000.0


def test_stale_signed_at_rejected_as_replay(monkeypatch):
    priv, pub = generate_keypair()
    monkeypatch.setenv("HERALD_BRIEFS_PUBKEY", pub)
    stale = sign_briefs({"items": [{"id": "0142", "funded": True}],
                         "signed_at": int(time.time()) - 3600}, priv)
    with pytest.raises(ValueError):
        verify_briefs(stale)  # validly signed, but an hour old -> replay, reject
    with pytest.raises(ValueError):
        verify_briefs(sign_briefs({"items": []}, priv))  # missing signed_at -> reject
    monkeypatch.setenv("HERALD_BRIEFS_MAX_AGE", "0")  # 0 disables the freshness gate
    assert verify_briefs(stale)["items"][0]["funded"] is True


def test_tampered_reward_pool_rejected(monkeypatch):
    # The whole point: a forged larger reward_pool (which would direct more emissions) fails verification.
    priv, pub = generate_keypair()
    signed = sign_briefs({"items": [{"id": "0142", "reward_pool": 1000.0}]}, priv)
    signed["items"][0]["reward_pool"] = 9000.0
    monkeypatch.setenv("HERALD_BRIEFS_PUBKEY", pub)
    with pytest.raises(ValueError):
        verify_briefs(signed)


def test_unsigned_mode_passes_through(monkeypatch):
    monkeypatch.delenv("HERALD_BRIEFS_PUBKEY", raising=False)
    monkeypatch.delenv("HERALD_REQUIRE_SIGNED_BRIEFS", raising=False)
    payload = {"items": [{"id": "0142"}]}
    assert verify_briefs(payload) is payload  # no pubkey -> trust-the-endpoint


def test_required_but_missing_raises(monkeypatch):
    monkeypatch.delenv("HERALD_BRIEFS_PUBKEY", raising=False)
    monkeypatch.setenv("HERALD_REQUIRE_SIGNED_BRIEFS", "true")
    with pytest.raises(ValueError):
        verify_briefs({"items": []})
