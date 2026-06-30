import pytest

from herald.validator.news.registry_anchor import (
    content_hash, encode_anchor, parse_anchor, verify_anchor,
)

DATA = {"version_id": 7, "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["nytimes.com"]}]}


def test_encode_then_verify():
    anchor = encode_anchor(DATA["version_id"], content_hash(DATA), effective_block=5000)
    assert anchor.startswith("HRLDREG|7|")
    assert verify_anchor(DATA, anchor) is True


def test_tampered_version_rejected():
    anchor = encode_anchor(7, content_hash(DATA), 5000)
    assert verify_anchor({**DATA, "version_id": 8}, anchor) is False


def test_tampered_content_rejected():
    anchor = encode_anchor(7, content_hash(DATA), 5000)
    tampered = {**DATA, "outlets": DATA["outlets"] + [{"outlet_id": "x", "tier": 3, "domains": ["x.co"]}]}
    assert verify_anchor(tampered, anchor) is False


def test_malformed_anchor_rejected():
    assert verify_anchor(DATA, "garbage") is False
    assert verify_anchor(DATA, "") is False


def test_parse_anchor():
    a = parse_anchor("HRLDREG|7|abcdef|5000")
    assert a == {"version_id": 7, "hash": "abcdef", "effective_block": 5000}


def test_content_hash_ignores_signature():
    assert content_hash({**DATA, "signature": "zz"}) == content_hash(DATA)
