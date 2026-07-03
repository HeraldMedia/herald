from types import SimpleNamespace

from herald.miner.claim_store import ClaimStore
from herald.miner.commit import submit_commitment


def test_submit_writes_chain_and_store(tmp_path):
    calls = []
    subtensor = SimpleNamespace(set_commitment=lambda wallet, netuid, data: calls.append((netuid, data)) or True)
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkA"))
    store = ClaimStore(str(tmp_path / "c.json"))

    onchain = submit_commitment(
        subtensor, wallet, netuid=69, store=store,
        brief_id="b1", target_outlet_id="nyt", bond_atto=1000, version_id=1,
    )

    assert calls == [(69, onchain)]
    rec = store.get(onchain)
    assert rec["claimer_hotkey"] == "hkA" and rec["target_outlet_id"] == "nyt"
