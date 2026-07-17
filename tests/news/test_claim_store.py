import os

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


def test_active_claims_hot_reloads_external_changes(tmp_path):
    path = str(tmp_path / "claims.json")
    serving_store = ClaimStore(path)
    cli_store = ClaimStore(path)

    onchain = cli_store.add(**REC)
    cli_store.set_article_url(onchain, "https://www.nytimes.com/hot-reload")

    assert serving_store.active_claims()[0]["article_url"].endswith("/hot-reload")


def test_active_claims_notices_removed_store(tmp_path):
    path = str(tmp_path / "claims.json")
    store = ClaimStore(path)
    onchain = store.add(**REC)
    store.set_article_url(onchain, "https://www.nytimes.com/a")
    assert len(store.active_claims()) == 1

    os.unlink(path)

    assert store.active_claims() == []


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


def test_add_with_evidence_binds_pre_hash(tmp_path):
    from herald.commit import commit_hash, encode
    from herald.evidence import evidence_hash

    store = ClaimStore(str(tmp_path / "claims.json"))
    evidence = {"text": "Exclusive draft copy the miner supplied to the outlet.", "author": "Jane Doe"}
    onchain = store.add(**REC, evidence=evidence)
    rec = store.get(onchain)
    assert rec["pre_hash"] == evidence_hash(rec["evidence"])
    assert onchain == encode(commit_hash(
        brief_id=rec["brief_id"], target_outlet_id=rec["target_outlet_id"],
        claimer_hotkey=rec["claimer_hotkey"], nonce=rec["nonce"],
        bond_atto=rec["bond_atto"], version_id=rec["version_id"], pre_hash=rec["pre_hash"],
    ))


def test_import_record_rejects_tampered_evidence(tmp_path):
    import pytest

    src = ClaimStore(str(tmp_path / "src.json"))
    onchain = src.add(**REC, evidence={"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]})
    rec = src.get(onchain)
    reveal = {**rec, "onchain": onchain, "article_url": "https://x/a"}

    dst = ClaimStore(str(tmp_path / "dst.json"))
    assert dst.import_record(dict(reveal)) == onchain  # intact round-trip
    assert dst.get(onchain)["evidence"]["author"] == "Jane Doe"

    with pytest.raises(ValueError):
        dst.import_record({**reveal, "evidence": {**reveal["evidence"], "author": "Someone Else"}})
    with pytest.raises(ValueError):
        dst.import_record({**reveal, "pre_hash": "ab" * 24})


def test_claim_snapshot_stored_and_reimported(tmp_path):
    store = ClaimStore(str(tmp_path / "claims.json"))
    onchain = store.add(**REC)
    store.set_article_url(onchain, "https://www.nytimes.com/a", snapshot_text="Extracted article text.")
    rec = store.get(onchain)
    assert rec["snapshot_text"] == "Extracted article text."

    dst = ClaimStore(str(tmp_path / "dst.json"))
    dst.import_record({**rec, "onchain": onchain})
    assert dst.get(onchain)["snapshot_text"] == "Extracted article text."
