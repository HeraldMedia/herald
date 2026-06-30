from herald.commit import commit_hash, encode
from herald.miner.claim_store import ClaimStore

REC = dict(brief_id="b1", target_outlet_id="nyt", claimer_hotkey="hkA",
           bond_atto=1000, version_id=1)


def test_add_returns_commit_value_and_persists(tmp_path):
    store = ClaimStore(str(tmp_path / "claims.json"))
    onchain = store.add(**REC)
    rec = store.get(onchain)
    assert onchain == encode(commit_hash(nonce=rec["nonce"], **REC))
    assert rec["article_url"] is None


def test_active_claims_only_after_url_set(tmp_path):
    store = ClaimStore(str(tmp_path / "claims.json"))
    onchain = store.add(**REC)
    assert store.active_claims() == []
    store.set_article_url(onchain, "https://www.nytimes.com/a")
    active = store.active_claims()
    assert len(active) == 1 and active[0]["article_url"] == "https://www.nytimes.com/a"


def test_reload_from_disk(tmp_path):
    path = str(tmp_path / "claims.json")
    onchain = ClaimStore(path).add(**REC)
    ClaimStore(path).set_article_url(onchain, "https://www.nytimes.com/a")
    assert len(ClaimStore(path).active_claims()) == 1


def test_unique_nonce_per_add(tmp_path):
    store = ClaimStore(str(tmp_path / "claims.json"))
    a = store.add(**REC)
    b = store.add(**REC)
    assert a != b  # fresh nonce each time


def test_claim_store_atomic_and_private(tmp_path):
    import os
    import stat
    path = str(tmp_path / "claims.json")
    ClaimStore(path).add(**REC)
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [] and os.path.exists(path)
    # the nonce is the commit salt: the file must not be world/group-readable
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert len(ClaimStore(path)._records) == 1  # reloads cleanly
