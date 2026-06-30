from herald.commit import commit_hash, encode, matches

FIELDS = dict(
    brief_id="b1",
    target_outlet_id="nyt",
    claimer_hotkey="5Haaa",
    nonce="abc123",
    bond_atto=1000,
    version_id=1,
)


def test_commit_hash_deterministic_hex():
    h1 = commit_hash(**FIELDS)
    h2 = commit_hash(**FIELDS)
    assert h1 == h2 and len(h1) == 48 and all(c in "0123456789abcdef" for c in h1)


def test_nonce_changes_hash():
    other = {**FIELDS, "nonce": "different"}
    assert commit_hash(**other) != commit_hash(**FIELDS)


def test_encode_prefixed():
    assert encode(commit_hash(**FIELDS)).startswith("HRLD1|")


def test_matches_roundtrip():
    onchain = encode(commit_hash(**FIELDS))
    assert matches(onchain, **FIELDS) is True
    assert matches(onchain, **{**FIELDS, "bond_atto": 999}) is False


def test_matches_rejects_unrelated_value():
    assert matches("HRLD1|deadbeef", **FIELDS) is False
