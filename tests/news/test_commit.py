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


def test_no_separator_injection_collision():
    # moving the boundary between fields must NOT collide
    a = commit_hash(brief_id="b1\x1fnyt", target_outlet_id="x", claimer_hotkey="hk",
                    nonce="n", bond_atto=1, version_id=1)
    b = commit_hash(brief_id="b1", target_outlet_id="nyt\x1fx", claimer_hotkey="hk",
                    nonce="n", bond_atto=1, version_id=1)
    assert a != b


def test_empty_pre_hash_is_legacy_hash():
    # Golden back-compat: an evidence-less commit must hash byte-identically to the original
    # format, so every commitment sealed before the evidence upgrade keeps verifying.
    assert commit_hash(**FIELDS, pre_hash="") == commit_hash(**FIELDS)
    assert commit_hash(**FIELDS) == "e58cc27d0ae7ea7c4e7793ece4b161dfff42140a33500cfa"


def test_pre_hash_changes_hash_and_roundtrips():
    withev = {**FIELDS, "pre_hash": "ab" * 24}
    assert commit_hash(**withev) != commit_hash(**FIELDS)
    onchain = encode(commit_hash(**withev))
    assert matches(onchain, **withev) is True
    assert matches(onchain, **FIELDS) is False  # dropping the evidence must not verify
    assert matches(onchain, **{**withev, "pre_hash": "cd" * 24}) is False
