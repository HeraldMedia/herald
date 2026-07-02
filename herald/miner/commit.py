"""Submit a commitment on chain and record its reveal locally."""

from .claim_store import ClaimStore


def submit_commitment(
    subtensor, wallet, netuid: int, store: ClaimStore,
    *, brief_id: str, target_outlet_id: str, bond_atto: int, version_id: int,
    evidence: dict = None,
) -> str:
    onchain = store.add(
        brief_id=brief_id,
        target_outlet_id=target_outlet_id,
        claimer_hotkey=wallet.hotkey.ss58_address,
        bond_atto=bond_atto,
        version_id=version_id,
        evidence=evidence,
    )
    subtensor.commit(wallet, netuid, onchain)
    return onchain
