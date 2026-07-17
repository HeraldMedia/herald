from types import SimpleNamespace

from herald.validator.news import publish
from herald.validator.news.vesting import VestingLedger


def test_non_successful_result_publish_is_reported(monkeypatch):
    warnings = []

    class Response:
        def raise_for_status(self):
            raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(publish.httpx, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(publish.bt.logging, "warning", warnings.append)

    publish.publish_results("https://board.example", [{"article_id": "a"}])

    assert warnings and "401 Unauthorized" in warnings[0]


def test_result_projection_is_network_scoped_and_tracks_lifecycle():
    vesting = VestingLedger(vest_epochs=2)
    vesting.start(
        "article-1", uid=1, total_usd=500.0, url="https://example.com/a",
        hotkey="miner-hotkey", brief_id="brief-1", commit_epoch=10, start_epoch=11,
        outlet_id="example", tier=1, attribution=2, reveal={"nonce": "secret"},
    )
    vesting.release("article-1", epoch=11)

    items = publish.build_result_items(
        vesting, network="test", netuid=535, validator_hotkey="validator-hotkey",
        validator_uid=4, chain_block=1234, registry_version=7, consensus="fingerprint",
    )

    assert items == [{
        "article_id": "article-1", "hotkey": "miner-hotkey", "brief_id": "brief-1",
        "url": "https://example.com/a", "usd": 500.0, "earned_usd": 250.0,
        "installment_usd": 250.0, "remaining": 1, "status": "VESTING",
        "commit_epoch": 10, "start_epoch": 11, "network": "test", "netuid": 535,
        "validator_hotkey": "validator-hotkey", "validator_uid": 4,
        "chain_block": 1234, "registry_version": 7, "consensus": "fingerprint",
        "outlet_id": "example", "tier": 1, "attribution": 2,
        "reveal": {"nonce": "secret"},
    }]


def test_result_projection_keeps_terminal_entries_after_claim_disappears():
    vesting = VestingLedger(vest_epochs=2)
    vesting.start("article-1", uid=1, total_usd=100.0, hotkey="miner")
    vesting.clawback("article-1")

    items = publish.build_result_items(
        vesting, network="finney", netuid=69, validator_hotkey="validator",
        validator_uid=8, chain_block=99, registry_version=1, consensus="fp",
    )

    assert len(items) == 1 and items[0]["status"] == "CLAWBACK"


def test_epoch_snapshot_uses_exact_integer_accounting_and_normalized_u16():
    vesting = VestingLedger(vest_epochs=30)
    vesting.start("a1", uid=1, total_usd=500, hotkey="miner-1", brief_id="b1",
                  start_epoch=20)
    vesting.release("a1", 20)
    snapshot = publish.build_epoch_snapshot(
        vesting,
        [{"id": "b1", "kind": "client", "reward_pool": 700}],
        {"b1": 500 / 30},
        {1: 500 / 30, 2: 700 / 30},
        [0.4166666667, 0.5833333333], [1, 2], {1: "miner-1", 2: "miner-2"},
        network="test", netuid=535, validator_hotkey="validator", validator_uid=4,
        chain_block=1234, epoch=20, registry_version=3, registry_hash="a" * 48,
        consensus="fp",
    )

    assert snapshot["state"]["articles"][0]["earned_microusd"] == 16_666_667
    assert snapshot["state"]["briefs"][0]["pool_remaining_microusd"] == 683_333_333
    assert sum(row["weight_u16"] for row in snapshot["state"]["weights"]) == 65535
    assert len(snapshot["state_hash"]) == 64


def test_state_hash_ignores_validator_identity_and_evaluation_block():
    vesting = VestingLedger(vest_epochs=30)
    vesting.start("a1", uid=1, total_usd=500, url="https://example.com/a",
                  hotkey="miner-1", brief_id="b1", start_epoch=20)
    args = (vesting, [], {}, {}, [], [], {})
    common = dict(network="test", netuid=535, epoch=20, registry_version=3,
                  registry_hash="a" * 48, consensus="fp")
    one = publish.build_epoch_snapshot(*args, validator_hotkey="v1", validator_uid=4,
                                       chain_block=1234, **common)
    two = publish.build_epoch_snapshot(*args, validator_hotkey="v2", validator_uid=5,
                                       chain_block=1236, **common)
    assert one["state_hash"] == two["state_hash"]
    assert "validator_hotkey" not in one["state"]["articles"][0]
    assert "chain_block" not in one["state"]["articles"][0]
