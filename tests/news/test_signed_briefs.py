import pytest

from herald.validator.news.registry_signing import generate_keypair
from herald.validator.news.signed_briefs import sign_briefs, verify_briefs


def test_sign_verify_round_trip(monkeypatch):
    priv, pub = generate_keypair()
    payload = {"items": [{"id": "0142", "boost": 2.0}, {"id": "0143", "boost": 1.0}]}
    signed = sign_briefs(payload, priv)
    monkeypatch.setenv("HERALD_BRIEFS_PUBKEY", pub)
    assert verify_briefs(signed)["items"][0]["boost"] == 2.0


def test_tampered_boost_rejected(monkeypatch):
    # The whole point: a forged higher boost (which would direct more emissions) fails verification.
    priv, pub = generate_keypair()
    signed = sign_briefs({"items": [{"id": "0142", "boost": 1.0}]}, priv)
    signed["items"][0]["boost"] = 3.0
    monkeypatch.setenv("HERALD_BRIEFS_PUBKEY", pub)
    with pytest.raises(ValueError):
        verify_briefs(signed)


def test_unsigned_mode_passes_through(monkeypatch):
    monkeypatch.delenv("HERALD_BRIEFS_PUBKEY", raising=False)
    monkeypatch.delenv("HERALD_REQUIRE_SIGNED_BRIEFS", raising=False)
    payload = {"items": [{"id": "0142"}]}
    assert verify_briefs(payload) is payload  # no pubkey -> trust-the-endpoint (boost still clamped)


def test_required_but_missing_raises(monkeypatch):
    monkeypatch.delenv("HERALD_BRIEFS_PUBKEY", raising=False)
    monkeypatch.setenv("HERALD_REQUIRE_SIGNED_BRIEFS", "true")
    with pytest.raises(ValueError):
        verify_briefs({"items": []})
