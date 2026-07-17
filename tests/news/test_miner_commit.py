from types import SimpleNamespace

import pytest

from herald.miner.claim_store import ClaimStore
from herald.miner.cli import build_parser, cmd_commit
from herald.miner.commit import resubmit_commitment, submit_commitment


def test_submit_writes_chain_and_store(tmp_path):
    calls = []
    subtensor = SimpleNamespace(set_commitment=lambda wallet, netuid, data: calls.append((netuid, data)) or True)
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkA"))
    store = ClaimStore(str(tmp_path / "c.json"))

    onchain = submit_commitment(
        subtensor, wallet, netuid=69, store=store,
        brief_id="b1", target_outlet_id="nyt", bond_atto=0, version_id=1,
    )

    assert calls == [(69, onchain)]
    rec = store.get(onchain)
    assert rec["claimer_hotkey"] == "hkA" and rec["target_outlet_id"] == "nyt"
    assert rec["bond_atto"] == 0


def test_resubmit_reuses_exact_stored_commitment(tmp_path):
    calls = []
    subtensor = SimpleNamespace(set_commitment=lambda wallet, netuid, data: calls.append((netuid, data)) or True)
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkA"))
    store = ClaimStore(str(tmp_path / "c.json"))
    onchain = store.add(
        brief_id="b1", target_outlet_id="nyt", claimer_hotkey="hkA",
        bond_atto=0, version_id=1,
    )

    assert resubmit_commitment(subtensor, wallet, 69, store, onchain) == onchain
    assert calls == [(69, onchain)]
    assert len(store._records) == 1


def test_resubmit_rejects_commitment_from_another_hotkey(tmp_path):
    subtensor = SimpleNamespace(set_commitment=lambda *args: True)
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkB"))
    store = ClaimStore(str(tmp_path / "c.json"))
    onchain = store.add(
        brief_id="b1", target_outlet_id="nyt", claimer_hotkey="hkA",
        bond_atto=0, version_id=1,
    )

    with pytest.raises(ValueError, match="belongs to hotkey hkA"):
        resubmit_commitment(subtensor, wallet, 69, store, onchain)


def test_commit_cli_has_no_miner_bond_option():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["commit", "--brief", "b1", "--outlet", "nyt", "--bond", "1"])


def test_commit_cli_writes_zero_legacy_bond(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("herald.miner.cli.bt.Wallet", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr("herald.miner.cli.bt.Subtensor", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        "herald.miner.cli.submit_commitment",
        lambda *args, **kwargs: captured.update(kwargs) or "HRLD1|commit",
    )
    args = SimpleNamespace(
        text_file=None, quote=None, author=None, window=None,
        wallet_name="wallet", hotkey="miner", network="test", netuid=1,
        store=str(tmp_path / "claims.json"), brief="b1", outlet="nyt", version=2,
    )

    cmd_commit(args)

    assert captured["bond_atto"] == 0
