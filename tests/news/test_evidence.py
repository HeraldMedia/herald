import pytest

from herald.evidence import MAX_AUTHOR_CHARS, MAX_TEXT_CHARS, clean_evidence, evidence_hash

FULL = {"text": "The launch marks a first for verified media placement on Bittensor.",
        "author": "Jane Doe", "window": ["2026-07-10", "2026-07-20"]}


def test_hash_deterministic_and_key_order_independent():
    h1 = evidence_hash(clean_evidence(FULL))
    h2 = evidence_hash(clean_evidence({"window": FULL["window"], "author": "Jane Doe", "text": FULL["text"]}))
    assert h1 == h2 and len(h1) == 48 and all(c in "0123456789abcdef" for c in h1)


def test_any_field_change_changes_hash():
    base = evidence_hash(clean_evidence(FULL))
    assert evidence_hash(clean_evidence({**FULL, "text": FULL["text"] + " edited"})) != base
    assert evidence_hash(clean_evidence({**FULL, "author": "John Doe"})) != base
    assert evidence_hash(clean_evidence({**FULL, "window": ["2026-07-11", "2026-07-20"]})) != base


def test_clean_strips_empties_and_whitespace():
    assert clean_evidence(None) == {}
    assert clean_evidence({"text": "  ", "author": ""}) == {}
    assert clean_evidence({"author": "  Jane Doe "}) == {"author": "Jane Doe"}


def test_caps_enforced():
    with pytest.raises(ValueError):
        clean_evidence({"text": "x" * (MAX_TEXT_CHARS + 1)})
    with pytest.raises(ValueError):
        clean_evidence({"author": "a" * (MAX_AUTHOR_CHARS + 1)})


def test_window_validation():
    with pytest.raises(ValueError):
        clean_evidence({"window": ["2026-07-20", "2026-07-10"]})  # start after end
    with pytest.raises(ValueError):
        clean_evidence({"window": ["not-a-date", "2026-07-10"]})
    with pytest.raises(ValueError):
        clean_evidence({"window": ["2026-07-10"]})  # must be a pair
    assert clean_evidence({"window": ["2026-07-10", "2026-07-10"]})["window"] == ["2026-07-10", "2026-07-10"]
