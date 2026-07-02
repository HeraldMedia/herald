import pytest
from pydantic import ValidationError

from herald.protocol import ClaimRecord, ClaimSynapse, MAX_CLAIMS_PER_RESPONSE

BASE = dict(brief_id="b", target_outlet_id="o", article_url="https://x/a",
            claimer_hotkey="hk", nonce="n", bond_atto=1, version_id=1)


def test_normal_claim_and_list_ok():
    ClaimSynapse(claims=[ClaimRecord(**BASE)] * 3)


def test_claims_list_over_cap_rejected():
    # A miner controls this list entirely; an oversized list must be rejected at parse time
    # so the validator can't be OOM'd before its per-miner slice.
    with pytest.raises(ValidationError):
        ClaimSynapse(claims=[ClaimRecord(**BASE)] * (MAX_CLAIMS_PER_RESPONSE + 1))


def test_oversized_record_fields_rejected():
    with pytest.raises(ValidationError):
        ClaimRecord(**{**BASE, "bond_atto": 10 ** 40})       # absurd big-int
    with pytest.raises(ValidationError):
        ClaimRecord(**{**BASE, "merkle_path": ["x" * 10000]})  # giant item
    with pytest.raises(ValidationError):
        ClaimRecord(**{**BASE, "merkle_path": ["x"] * 10000})  # too many items


def test_evidence_fields_bounded():
    ok = ClaimRecord(**BASE, pre_hash="ab" * 24,
                     evidence_text="draft " * 100, evidence_author="Jane Doe",
                     evidence_window=["2026-07-10", "2026-07-15"])
    assert ok.pre_hash and ok.evidence_author == "Jane Doe"
    with pytest.raises(ValidationError):
        ClaimRecord(**BASE, evidence_text="x" * 20_001)
    with pytest.raises(ValidationError):
        ClaimRecord(**BASE, evidence_window=["a", "b", "c"])


def test_snapshot_text_bounded():
    assert ClaimRecord(**BASE, snapshot_text="x" * 30_000).snapshot_text
    with pytest.raises(ValidationError):
        ClaimRecord(**BASE, snapshot_text="x" * 30_001)
