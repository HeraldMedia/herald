"""Contract for placement-pool rows in the reconciliation feed.

fixtures/pool_board_row.json is the golden row the backend serves for a submitted placement. Its
"_expected" object is not part of the row: it holds the on-chain commitment value and the cleaned
evidence the row must reproduce, and is removed before the row is merged.

The row goes through the real merge_board_claims, commitment and evidence code, so a change on either
side of the contract fails here. To run the same checks against rows a backend actually served, set
HERALD_BOARD_ROWS_JSON to a saved feed body: the JSON list GET /validator/results returned, or
{"rows": [...], "commitments": {hotkey: on-chain value}} to check the commitments as well.
"""

import copy
import json
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from herald.commit import matches
from herald.evidence import clean_evidence, evidence_hash
from herald.protocol import ClaimRecord
from herald.validator.news.fetch import FetchResult
from herald.validator.news.oracle import evaluate_article
from herald.validator.news.publish import build_epoch_snapshot, build_result_items
from herald.validator.news.reconcile import merge_board_claims
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.url import article_id
from herald.validator.news.vesting import VestingLedger

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "pool_board_row.json")
ROWS_ENV = "HERALD_BOARD_ROWS_JSON"
UID = 7

# Golden values, pinned here too so the fixture cannot drift on its own.
PRE_HASH = "67db688c837f36b9654c3214aecae7fe8dc585db1926a306"
ONCHAIN = "HRLD1|36e2b586bc8063b24b0a4f40dbe2a1fc3f271ccbad33a8e4"
ARTICLE_ID = "ba76bc5c141d3a5437be2fbbec625160e1125cf7f08012385e9fbc43d5f0b7d9"

ROW_KEYS = {"network", "netuid", "hotkey", "brief_id", "url", "reveal"}
REVEAL_KEYS = {"target_outlet_id", "nonce", "bond_atto", "version_id", "pre_hash", "evidence_text", "claim_sig"}
OPTIONAL_REVEAL_KEYS = {"evidence_author", "evidence_window"}
EVIDENCE_KEYS = (("text", "evidence_text"), ("author", "evidence_author"), ("window", "evidence_window"))

REGISTRY = OutletRegistry.from_dict({
    "version_id": 3,
    "outlets": [{"outlet_id": "reuters", "tier": 1, "domains": ["reuters.com", "www.reuters.com"],
                 "fetch": "proxy"}],
})
BRIEF = {"id": "0123456789abcdef", "kind": "standing", "keywords": ["Acme"]}


def _golden():
    with open(FIXTURE, encoding="utf-8") as f:
        row = json.load(f)
    return row, row.pop("_expected")


def _served():
    """(rows, commitments by hotkey) from HERALD_BOARD_ROWS_JSON; skips when it is not set."""
    path = os.environ.get(ROWS_ENV, "").strip()
    if not path:
        pytest.skip(f"{ROWS_ENV} is not set")
    with open(path, encoding="utf-8") as f:
        body = json.load(f)
    if isinstance(body, dict):
        return body["rows"], body.get("commitments") or {}
    return body, {}


def _is_pool_row(row) -> bool:
    # Every ledger row, a published result or a snapshot article, is keyed by article_id. A pending
    # placement-pool row has none, whatever its reveal carries.
    return isinstance(row, dict) and "article_id" not in row


def _hex(value, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def _assert_row_shape(row):
    assert set(row) == ROW_KEYS
    reveal = row["reveal"]
    assert REVEAL_KEYS <= set(reveal) <= REVEAL_KEYS | OPTIONAL_REVEAL_KEYS
    assert isinstance(row["network"], str) and row["network"] and type(row["netuid"]) is int
    assert all(isinstance(row[key], str) and row[key] for key in ("hotkey", "brief_id", "url"))
    assert isinstance(reveal["target_outlet_id"], str) and reveal["target_outlet_id"]
    assert _hex(reveal["nonce"], 32) and _hex(reveal["pre_hash"], 48)
    assert type(reveal["bond_atto"]) is int and reveal["bond_atto"] == 0
    assert type(reveal["version_id"]) is int
    assert isinstance(reveal["evidence_text"], str) and reveal["evidence_text"]
    if "evidence_author" in reveal:
        assert isinstance(reveal["evidence_author"], str) and reveal["evidence_author"]
    if "evidence_window" in reveal:
        window = reveal["evidence_window"]
        assert isinstance(window, list) and len(window) == 2
        assert all(isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in window)
    assert isinstance(reveal["claim_sig"], str) and reveal["claim_sig"].startswith("0x")
    assert _hex(reveal["claim_sig"][2:], 128)


def _merge_one(row) -> ClaimRecord:
    claims_by_uid = {}
    assert merge_board_claims(claims_by_uid, [row], {UID: row["hotkey"]}) == 1
    (claim,) = claims_by_uid[UID]
    assert isinstance(claim, ClaimRecord)
    return claim


def _assert_claim_mirrors_row(claim, row):
    reveal = row["reveal"]
    assert claim.claimer_hotkey == row["hotkey"]
    assert (claim.brief_id, claim.article_url) == (row["brief_id"], row["url"])
    assert (claim.target_outlet_id, claim.nonce) == (reveal["target_outlet_id"], reveal["nonce"])
    assert (claim.bond_atto, claim.version_id) == (reveal["bond_atto"], reveal["version_id"])
    assert claim.pre_hash == reveal["pre_hash"]
    assert claim.evidence_text == reveal["evidence_text"]
    assert claim.evidence_author == reveal.get("evidence_author")
    assert claim.evidence_window == reveal.get("evidence_window")
    assert claim.snapshot_text is None


def _revealed_evidence(reveal) -> dict:
    return {field: reveal[key] for field, key in EVIDENCE_KEYS if key in reveal}


def _assert_evidence_hash(claim, reveal):
    # The row carries the cleaned strings byte for byte, so cleaning them again changes nothing.
    assert clean_evidence(_revealed_evidence(reveal)) == _revealed_evidence(reveal)
    rebuilt = clean_evidence({"text": claim.evidence_text, "author": claim.evidence_author,
                              "window": claim.evidence_window})
    assert evidence_hash(rebuilt) == claim.pre_hash


def _matches(onchain, claim) -> bool:
    return matches(onchain, brief_id=claim.brief_id, target_outlet_id=claim.target_outlet_id,
                   claimer_hotkey=claim.claimer_hotkey, nonce=claim.nonce, bond_atto=claim.bond_atto,
                   version_id=claim.version_id, pre_hash=claim.pre_hash or "")


def _variant(row, **reveal):
    """A copy of the row with reveal keys replaced; None removes the key."""
    changed = copy.deepcopy(row)
    for key, value in reveal.items():
        if value is None:
            changed["reveal"].pop(key, None)
        else:
            changed["reveal"][key] = value
    return changed


def _check_served_rows(rows):
    flags = [_is_pool_row(row) for row in rows]
    assert any(flags), "the feed holds no placement-pool rows"
    assert flags == sorted(flags, reverse=True), "placement-pool rows must come before every other row"
    for row in filter(_is_pool_row, rows):
        _assert_row_shape(row)
        claim = _merge_one(row)
        _assert_claim_mirrors_row(claim, row)
        _assert_evidence_hash(claim, row["reveal"])


def _check_served_commitments(rows, commitments):
    pool_rows = list(filter(_is_pool_row, rows))
    assert pool_rows, "the feed holds no placement-pool rows"
    for row in pool_rows:
        assert row["hotkey"] in commitments
        assert _matches(commitments[row["hotkey"]], _merge_one(row))


def _ledger_rows(row):
    """The same placement once scored: a published result row and a snapshot article row."""
    vesting = VestingLedger(vest_epochs=30)
    vesting.start(ARTICLE_ID, uid=UID, total_usd=100.0, url=row["url"], hotkey=row["hotkey"],
                  brief_id=row["brief_id"], commit_epoch=1254, start_epoch=1255, outlet_id="reuters",
                  tier=1, attribution=2, reveal=copy.deepcopy(row["reveal"]))
    scope = dict(network=row["network"], netuid=row["netuid"], validator_hotkey="5Validator",
                 validator_uid=0, registry_version=3, consensus="fp")
    (result,) = build_result_items(vesting, chain_block=9_036_410, **scope)
    snapshot = build_epoch_snapshot(vesting, [], {}, {}, [], [], {}, chain_block=9_036_410, epoch=1255,
                                    registry_hash="a" * 48, **scope)
    (article,) = snapshot["state"]["articles"]
    return [result, article]


def test_golden_row_has_the_feed_shape():
    row, expected = _golden()
    _assert_row_shape(row)
    assert (row["network"], row["netuid"]) == ("finney", 69)
    assert row["reveal"]["pre_hash"] == PRE_HASH
    assert set(expected) == {"onchain", "evidence"} and expected["onchain"] == ONCHAIN
    assert _revealed_evidence(row["reveal"]) == expected["evidence"]


def test_golden_row_merges_into_one_claim_record():
    row, _ = _golden()
    claim = _merge_one(row)
    _assert_claim_mirrors_row(claim, row)
    assert (claim.bond_atto, claim.version_id, claim.pre_hash) == (0, 3, PRE_HASH)
    assert article_id(claim.article_url) == ARTICLE_ID


def test_golden_row_reproduces_its_evidence_hash_and_commitment():
    row, expected = _golden()
    claim = _merge_one(row)
    _assert_evidence_hash(claim, row["reveal"])
    assert evidence_hash(clean_evidence(expected["evidence"])) == PRE_HASH
    assert _matches(expected["onchain"], claim)


def test_golden_row_verifies_through_the_oracle():
    row, expected = _golden()
    claim = _merge_one(row)
    evidence = expected["evidence"]
    page = FetchResult(ok=True, status=200, final_url=row["url"], text_hash="h", body_len=4000,
                       text=f"Acme Corp reports. {evidence['text']} Shares rose in early trading.",
                       published_ts=datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc).timestamp(),
                       author=evidence["author"])
    indexed = lambda url: SimpleNamespace(in_index=True, matched_url=url, num_results=1, query=url)

    result = evaluate_article(claim, expected["onchain"], REGISTRY, BRIEF, fetch_fn=lambda url: page,
                              search_fn=indexed, serving_hotkey=row["hotkey"])

    assert (result.passed, result.reason) == (True, "ok")
    assert result.evidence["commitment"] is True and result.evidence["outlet_id"] == "reuters"
    assert result.evidence["attribution_level"] == 2
    assert result.article_id == ARTICLE_ID


def test_nested_evidence_object_is_not_read():
    row, expected = _golden()
    nested = _variant(row, evidence_text=None, evidence_author=None, evidence_window=None,
                      evidence=expected["evidence"])
    claim = _merge_one(nested)
    assert (claim.evidence_text, claim.evidence_author, claim.evidence_window) == (None, None, None)
    assert claim.pre_hash == PRE_HASH


@pytest.mark.parametrize("reveal", [
    {"evidence_window": "2026-09-14:2026-09-18"},
    {"evidence_author": "A" * 121},
    {"evidence_text": "x" * 20_001},
], ids=["window-as-string", "author-121-chars", "text-20001-chars"])
def test_rows_outside_claim_record_bounds_are_not_merged(reveal):
    row, _ = _golden()
    claims_by_uid = {}
    assert merge_board_claims(claims_by_uid, [_variant(row, **reveal)], {UID: row["hotkey"]}) == 0
    assert claims_by_uid == {}


def test_missing_version_id_merges_as_version_zero():
    row, expected = _golden()
    claim = _merge_one(_variant(row, version_id=None))
    assert claim.version_id == 0
    assert not _matches(expected["onchain"], claim)


def test_served_row_checks_tell_pool_rows_from_ledger_rows():
    row, expected = _golden()
    ledger = _ledger_rows(row)
    assert all(entry["reveal"] == row["reveal"] for entry in ledger)

    _check_served_rows([row] + ledger)
    _check_served_commitments([row] + ledger, {row["hotkey"]: expected["onchain"]})
    with pytest.raises(AssertionError, match="must come before every other row"):
        _check_served_rows([ledger[0], row, ledger[1]])
    with pytest.raises(AssertionError, match="holds no placement-pool rows"):
        _check_served_rows(ledger)


def test_served_pool_rows_follow_the_contract():
    rows, _ = _served()
    _check_served_rows(rows)


def test_served_pool_rows_match_their_commitments():
    rows, commitments = _served()
    if not commitments:
        pytest.skip(f"{ROWS_ENV} carries no commitments")
    _check_served_commitments(rows, commitments)
