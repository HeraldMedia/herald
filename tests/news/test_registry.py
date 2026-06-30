import json

import pytest

from herald.validator.news.registry import OutletRegistry, load_registry
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
