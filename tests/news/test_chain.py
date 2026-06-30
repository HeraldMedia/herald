from herald.validator.news import chain as chainmod
from herald.validator.news.chain import get_commitments_with_block


def test_extracts_value_and_onchain_block(monkeypatch):
    # mock the substrate decode helpers and the query_map iterator
    monkeypatch.setattr(chainmod, "decode_account_id", lambda x: x)
    monkeypatch.setattr(chainmod, "decode_metadata", lambda v: v["data"])

    entries = [
        (["hkA"], {"data": "HRLD1|aaa", "block": 1000}),
        (["hkB"], {"data": "HRLD1|bbb", "block": 1360}),
    ]
    subtensor = type("S", (), {"query_map": lambda self, **kw: iter(entries)})()

    out = get_commitments_with_block(subtensor, netuid=69)
    assert out == {"hkA": ("HRLD1|aaa", 1000), "hkB": ("HRLD1|bbb", 1360)}


def test_skips_undecodable_entries(monkeypatch):
    monkeypatch.setattr(chainmod, "decode_account_id", lambda x: x)

    def bad_decode(v):
        if v.get("data") is None:
            raise ValueError("bad")
        return v["data"]

    monkeypatch.setattr(chainmod, "decode_metadata", bad_decode)
    entries = [(["hkA"], {"data": "ok", "block": 1}), (["hkB"], {"data": None, "block": 2})]
    subtensor = type("S", (), {"query_map": lambda self, **kw: iter(entries)})()

    assert get_commitments_with_block(subtensor, netuid=69) == {"hkA": ("ok", 1)}
