import json

import pytest

from herald.validator.news.registry import OutletRegistry, load_registry
from herald.validator.news.registry_anchor import content_hash, encode_anchor
from herald.validator.news.registry_signing import generate_keypair, sign

OUTLETS = {
    "version_id": 1,
    "outlets": [
        {"outlet_id": "nyt", "tier": 1, "domains": ["nytimes.com", "www.nytimes.com"]},
        {"outlet_id": "blog", "tier": 3, "domains": ["bigsite.com"],
         "section_patterns": ["^/news/"]},
    ],
}


@pytest.fixture
def reg():
    return OutletRegistry.from_dict(OUTLETS)


def test_lookup_returns_outlet_and_tier(reg):
    o = reg.lookup("https://www.nytimes.com/2026/01/01/world/x")
    assert o is not None and o.outlet_id == "nyt" and o.tier == 1


def test_unknown_domain_returns_none(reg):
    assert reg.lookup("https://randomfarm.example/x") is None


def test_section_pattern_gates_path(reg):
    assert reg.lookup("https://bigsite.com/news/story-1") is not None
    assert reg.lookup("https://bigsite.com/sports/story-1") is None


def test_version_id_exposed(reg):
    assert reg.version_id == 1
    assert reg.content_hash == content_hash(OUTLETS)


def test_outlet_id_for_url(reg):
    assert reg.lookup("https://nytimes.com/a").outlet_id == "nyt"


def test_load_registry_verifies_signature(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    data = dict(OUTLETS)
    data["signature"] = sign(data, priv)
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    monkeypatch.setenv("HERALD_REGISTRY_PUBKEY", pub)
    assert load_registry().lookup("https://www.nytimes.com/a").tier == 1


def test_load_registry_rejects_bad_signature(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    data = dict(OUTLETS)
    data["signature"] = sign(data, priv)
    data["version_id"] = 999  # tamper after signing
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    monkeypatch.setenv("HERALD_REGISTRY_PUBKEY", pub)
    with pytest.raises(ValueError):
        load_registry()


def test_require_signed_registry_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(OUTLETS))  # unsigned
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    monkeypatch.setenv("HERALD_REQUIRE_SIGNED_REGISTRY", "true")
    with pytest.raises(ValueError):
        load_registry()


def test_load_registry_checks_onchain_anchor(tmp_path, monkeypatch):
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(OUTLETS))
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    good = encode_anchor(OUTLETS["version_id"], content_hash(OUTLETS), effective_block=10)
    assert load_registry(anchor_value=good).version_id == OUTLETS["version_id"]
    with pytest.raises(ValueError):
        load_registry(anchor_value="HRLDREG|1|deadbeef|10")


def test_required_onchain_anchor_fails_closed_when_missing(tmp_path, monkeypatch):
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(OUTLETS))
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    with pytest.raises(ValueError, match="anchor required"):
        load_registry(require_anchor=True)


def test_future_anchor_keeps_previous_signed_edition_until_effective_block(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    current = {**OUTLETS, "signature": sign(OUTLETS, priv)}
    future_unsigned = {**OUTLETS, "version_id": 2}
    future = {**future_unsigned, "signature": sign(future_unsigned, priv)}
    path = tmp_path / "outlets.json"
    path.write_text(json.dumps(current))
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(path))
    monkeypatch.setenv("HERALD_REGISTRY_PUBKEY", pub)
    anchor = encode_anchor(2, content_hash(future), effective_block=100)

    assert load_registry(anchor, require_anchor=True, current_block=99).version_id == 1
    with pytest.raises(ValueError, match="anchor mismatch"):
        load_registry(anchor, require_anchor=True, current_block=100)


def test_remote_registry_is_verified_before_cache(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    signed = {**OUTLETS, "signature": sign(OUTLETS, priv)}
    anchor = encode_anchor(1, content_hash(signed), effective_block=10)
    source = tmp_path / "fallback.json"
    source.write_text(json.dumps({"version_id": 0, "outlets": []}))
    cache = tmp_path / "verified.json"

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return signed

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setenv("HERALD_REGISTRY_ENDPOINT", "https://backend.example")
    monkeypatch.setenv("HERALD_REGISTRY_PATH", str(source))
    monkeypatch.setenv("HERALD_REGISTRY_CACHE_PATH", str(cache))
    monkeypatch.setenv("HERALD_REGISTRY_PUBKEY", pub)

    loaded = load_registry(anchor_value=anchor, require_anchor=True)
    assert loaded.version_id == 1
    assert json.loads(cache.read_text())["signature"] == signed["signature"]
