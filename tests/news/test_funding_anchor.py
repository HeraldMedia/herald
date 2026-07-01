from herald.validator.news.funding_anchor import (
    brief_id_hash,
    encode_funding,
    matches,
    parse_funding,
)


def test_encode_parse_round_trip():
    v = encode_funding("0142")
    assert v.startswith("HRLDFUND|") and len(v.split("|")[1]) == 48
    assert parse_funding(v) == brief_id_hash("0142")
    assert matches("0142", v)


def test_matches_distinguishes_briefs():
    v = encode_funding("0142")
    assert matches("0142", v)
    assert not matches("0143", v)


def test_parse_rejects_bad_values():
    assert parse_funding("HRLD1|" + "a" * 48) is None        # miner commit
    assert parse_funding("HRLDDIS|" + "a" * 48) is None       # dispute commit, not funding
    assert parse_funding("HRLDFUND|" + "z" * 48) is None      # right length, not hex
    assert parse_funding("HRLDFUND|abc") is None              # wrong length
    assert parse_funding("HRLDFUND|" + "a" * 48 + "|x") is None
    assert parse_funding(None) is None and parse_funding(7) is None
