from herald.validator.news.commit_index import CommitIndex


def test_records_onchain_block():
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": ("v1", 100)})
    idx.observe({"hkA": ("v1", 105)})  # block can only be the chain block; min kept
    assert idx.first_seen_block("hkA", "v1") == 100


def test_commit_epoch_quantizes():
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": ("v1", 105)})
    assert idx.commit_epoch("hkA", "v1") == 10


def test_unknown_returns_none():
    idx = CommitIndex(epoch_len=10)
    assert idx.commit_epoch("hkA", "v1") is None


def test_overwritten_value_tracked_independently():
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": ("v1", 100)})
    idx.observe({"hkA": ("v2", 200)})  # miner overwrote slot
    assert idx.first_seen_block("hkA", "v1") == 100
    assert idx.first_seen_block("hkA", "v2") == 200


def test_out_of_order_observation_keeps_min():
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": ("v1", 200)})
    idx.observe({"hkA": ("v1", 150)})
    assert idx.first_seen_block("hkA", "v1") == 150


def test_serialize_roundtrip():
    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": ("v1", 100)})
    restored = CommitIndex.from_dict(idx.to_dict())
    assert restored.first_seen_block("hkA", "v1") == 100
    assert restored.epoch_len == 10
