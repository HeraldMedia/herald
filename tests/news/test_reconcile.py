from types import SimpleNamespace

from herald.commit import commit_hash, encode
from herald.protocol import ClaimRecord
from herald.validator.news.commit_index import CommitIndex
from herald.validator.news.reconcile import fetch_board_results, merge_board_claims
from herald.validator.news.registry import OutletRegistry
from herald.validator.news.reward import score_claims
from herald.validator.utils.config import HERALD_ATTR_MULT, HERALD_BASE_PAYOUT_USD

REGISTRY = OutletRegistry.from_dict({
    "version_id": 1,
    "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["www.nytimes.com"]}],
})
BRIEFS = [{"id": "b1", "kind": "standing"}]
HK_BY_UID = {1: "hkA"}

FIELDS = dict(brief_id="b1", target_outlet_id="nyt", claimer_hotkey="hkA",
              nonce="n1", bond_atto=0, version_id=1)


def board_row(**over):
    base = dict(
        article_id="www.nytimes.com/a", hotkey="hkA", brief_id="b1",
        url="https://www.nytimes.com/a", usd=150.0, status="VESTING",
        reveal={"target_outlet_id": "nyt", "nonce": "n1", "bond_atto": 0, "version_id": 1},
    )
    base.update(over)
    return base


def test_withheld_claim_merged_and_pays_end_to_end():
    # The miner served another validator (which published) but not this one: the merged reveal
    # must re-verify against the chain slot and score like a dendrite-pulled claim.
    onchain = encode(commit_hash(**FIELDS))
    claims_by_uid = {1: []}
    added = merge_board_claims(claims_by_uid, [board_row()], HK_BY_UID)
    assert added == 1 and isinstance(claims_by_uid[1][0], ClaimRecord)

    idx = CommitIndex(epoch_len=10)
    idx.observe({"hkA": (onchain, 100)})
    live = lambda u: SimpleNamespace(ok=True, status=200, text_hash="h", body_len=2000,
                                     final_url=u, text="A normal news report.")
    indexed = lambda u: SimpleNamespace(in_index=True, matched_url=u, num_results=5, query=u)
    usd = score_claims(claims_by_uid, {"hkA": onchain}, idx, HK_BY_UID, BRIEFS, REGISTRY,
                       fetch_fn=live, search_fn=indexed)
    assert usd[1] == HERALD_BASE_PAYOUT_USD * HERALD_ATTR_MULT[0]


def test_already_served_claim_not_duplicated():
    served = ClaimRecord(brief_id="b1", target_outlet_id="nyt",
                         article_url="https://www.nytimes.com/a?utm_source=x",  # same canonical id
                         claimer_hotkey="hkA", nonce="n1", bond_atto=0, version_id=1)
    claims_by_uid = {1: [served]}
    assert merge_board_claims(claims_by_uid, [board_row()], HK_BY_UID) == 0
    assert len(claims_by_uid[1]) == 1


def test_unknown_hotkey_legacy_and_malformed_rows_skipped():
    rows = [
        board_row(hotkey="hkGONE"),                      # not in the metagraph
        {k: v for k, v in board_row().items() if k != "reveal"},  # pre-hardening row
        board_row(reveal="junk"),                        # malformed reveal
        board_row(reveal={"target_outlet_id": "nyt", "nonce": "n", "bond_atto": 10**40,  # over cap
                          "version_id": 1}),
        "not-a-dict",
    ]
    claims_by_uid = {1: []}
    assert merge_board_claims(claims_by_uid, rows, HK_BY_UID) == 0


def test_per_miner_cap_respected():
    rows = [board_row(url=f"https://www.nytimes.com/{i}", article_id=f"www.nytimes.com/{i}")
            for i in range(5)]
    claims_by_uid = {1: []}
    assert merge_board_claims(claims_by_uid, rows, HK_BY_UID, max_per_miner=3) == 3
    assert len(claims_by_uid[1]) == 3


def test_fetch_board_results_uses_private_feed_and_token(monkeypatch):
    seen = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [board_row()]

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setenv("HERALD_RESULTS_TOKEN", "validator-secret")
    monkeypatch.setattr("httpx.get", get)
    assert fetch_board_results("http://board") == [board_row()]
    assert seen["url"] == "http://board/validator/results"
    assert seen["headers"] == {"X-Results-Token": "validator-secret"}
