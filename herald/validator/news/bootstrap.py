"""Bootstrap a joining validator's vesting state from the board's public results feed.

A validator starting mid-flight has an empty herald_state.json: it would pay no installments on
existing placements, re-draw exhausted pools and diverge from incumbents for a full vest window.
This reconstructs an approximate ledger from /public/articles (each row carries usd, status and —
since the multi-validator hardening — start_epoch/commit_epoch published by incumbents).

The import is forward-only by design: everything accrued before the join is marked already
released (last_release_epoch = current epoch), so the joiner never retro-pays; it releases future
installments in step with incumbents and converges as old vests expire.

Usage:  python -m herald.validator.news.bootstrap \
            --results-url http://board:8093 --state-path ~/.bittensor/.../herald_state.json \
            --netuid 69 --network finney [--force]
"""

from herald.validator.utils.config import VEST_EPOCHS

from .state import HeraldState
from .vesting import VESTING


def bootstrap_state(rows, hotkey_to_uid: dict, current_epoch: int,
                    vest_epochs: int = VEST_EPOCHS) -> HeraldState:
    """Build a HeraldState from published result rows. Pure — testable without chain/network."""
    state = HeraldState.fresh()
    for row in rows:
        try:
            article_id = str(row["article_id"])
            hotkey = str(row["hotkey"])
            brief_id = str(row.get("brief_id") or "")
            usd = float(row["usd"])
            start_epoch = int(row["start_epoch"])
            commit_epoch = int(row.get("commit_epoch") or 0)
            status = str(row.get("status") or "")
        except (KeyError, TypeError, ValueError):
            continue  # pre-hardening rows lack start_epoch; nothing to reconstruct
        if status != VESTING or usd <= 0:
            continue
        uid = hotkey_to_uid.get(hotkey)
        if uid is None:
            continue  # hotkey left the metagraph: unpayable either way

        # Assume alive every epoch since start: released-so-far = elapsed + 1 (release fires on
        # the start epoch itself). Incumbents that HELD some epochs have more remaining — the
        # joiner under-pays slightly rather than over-paying, and both converge at expiry.
        released = min(vest_epochs, max(0, current_epoch - start_epoch + 1))
        remaining = vest_epochs - released
        installment = usd / vest_epochs
        state.pool_spent[brief_id] = state.pool_spent.get(brief_id, 0.0) + released * installment
        if remaining <= 0:
            continue  # fully vested from the joiner's perspective

        state.vesting.start(article_id, uid, usd, str(row.get("url") or ""), hotkey, brief_id,
                            commit_epoch, start_epoch)
        entry = state.vesting.entry(article_id)
        entry.remaining = remaining
        entry.last_release_epoch = current_epoch  # forward-only: no retro pay
    state.last_scored_epoch = current_epoch  # don't re-score the join epoch
    return state


def main():
    import argparse
    import os

    import bittensor as bt
    import httpx

    from herald.validator.utils.config import HERALD_EPOCH_LAG, VEST_EPOCH_LEN

    p = argparse.ArgumentParser(prog="herald-bootstrap")
    p.add_argument("--results-url", required=True, help="board base URL (serves /public/articles)")
    p.add_argument("--state-path", required=True, help="herald_state.json destination")
    p.add_argument("--netuid", type=int, default=69)
    p.add_argument("--network", default="finney")
    p.add_argument("--force", action="store_true", help="overwrite an existing state file")
    args = p.parse_args()

    if os.path.exists(args.state_path) and not args.force:
        raise SystemExit(f"{args.state_path} exists — refusing to overwrite (use --force)")

    resp = httpx.get(f"{args.results_url.rstrip('/')}/public/articles", timeout=30.0)
    resp.raise_for_status()
    rows = resp.json()

    subtensor = bt.subtensor(network=args.network)
    metagraph = subtensor.metagraph(args.netuid)
    hotkey_to_uid = {hk: uid for uid, hk in enumerate(metagraph.hotkeys)}
    current_epoch = max(0, subtensor.get_current_block() - HERALD_EPOCH_LAG) // VEST_EPOCH_LEN

    state = bootstrap_state(rows, hotkey_to_uid, current_epoch)
    state.save(args.state_path)
    n = len(state.vesting.active_article_ids())
    print(f"bootstrapped {n} vesting placement(s) at epoch {current_epoch} -> {args.state_path}")


if __name__ == "__main__":
    main()
