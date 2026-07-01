from herald.validator.news.dispute_anchor import (
    article_id_hash,
    encode_dispute,
    matches,
    parse_dispute,
)


def test_encode_parse_round_trip():
    aid = "https://reuters.com/technology/bittensor-pilot"
    v = encode_dispute(aid)
    assert v.startswith("HRLDDIS|") and len(v.split("|")[1]) == 48
    assert parse_dispute(v) == article_id_hash(aid)
    assert matches(aid, v)


def test_matches_distinguishes_articles():
    v = encode_dispute("https://a.com/x")
    assert matches("https://a.com/x", v)
    assert not matches("https://b.com/y", v)


def test_parse_rejects_bad_values():
    assert parse_dispute("HRLD1|" + "a" * 48) is None          # miner commit, not a dispute
    assert parse_dispute("HRLDDIS|" + "z" * 48) is None         # right length, not hex
    assert parse_dispute("HRLDDIS|abc") is None                 # wrong length
    assert parse_dispute("HRLDDIS|" + "a" * 48 + "|extra") is None
    assert parse_dispute(None) is None
    assert parse_dispute(123) is None
