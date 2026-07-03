"""Operator CLI: read briefs, commit to an outlet, attach the published URL."""

import argparse

import bittensor as bt

from herald.miner.claim_store import ClaimStore
from herald.miner.commit import submit_commitment
from herald.validator.utils.briefs import get_briefs


def cmd_briefs(args):
    for b in get_briefs():
        print(f"{b['id']}\t{b.get('title', '')}\t{b.get('start_date')}..{b.get('end_date')}")


def _build_evidence(args) -> dict:
    """Attribution evidence: pre-publication knowledge hashed into the commitment (see
    herald/evidence.py). Text proof (draft or quote) pays full; byline+window pays 0.7x;
    a bare commit pays the level-0 multiplier."""
    evidence = {}
    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            evidence["text"] = f.read()
    elif args.quote:
        evidence["text"] = args.quote
    if args.author:
        evidence["author"] = args.author
    if args.window:
        try:
            start, end = args.window.split(":")
        except ValueError:
            raise SystemExit("--window must be START:END, e.g. 2026-07-10:2026-07-20")
        evidence["window"] = [start, end]
    return evidence


def cmd_commit(args):
    evidence = _build_evidence(args)
    wallet = bt.Wallet(name=args.wallet_name, hotkey=args.hotkey)
    subtensor = bt.Subtensor(network=args.network)
    onchain = submit_commitment(
        subtensor, wallet, args.netuid, ClaimStore(args.store),
        brief_id=args.brief, target_outlet_id=args.outlet,
        bond_atto=args.bond, version_id=args.version,
        evidence=evidence,
    )
    if evidence.get("text"):
        level = "2 (text proof)"
    elif evidence.get("author") and evidence.get("window"):
        level = "1 (byline + window)"
    else:
        level = "0 (bare — pays the reduced level-0 multiplier)"
    print(f"committed: {onchain}")
    print(f"evidence level if it verifies at claim: {level}")


def cmd_claim(args):
    # Snapshot the article's extracted text with the claim: validators anchor it against their
    # own fetch, then run the content checks on these identical bytes so the whole fleet grades
    # the claim the same way (no per-validator page-variant forks).
    snapshot = None
    if args.snapshot_file:
        with open(args.snapshot_file, "r", encoding="utf-8") as f:
            snapshot = f.read()
    elif not args.no_snapshot:
        try:
            import httpx

            from herald.validator.news.fetch import _extract_text

            resp = httpx.get(args.url, timeout=20.0, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (herald-miner)"})
            resp.raise_for_status()
            snapshot = _extract_text(resp.text)
        except Exception as e:
            print(f"snapshot fetch failed ({e}); claiming without one")
    ClaimStore(args.store).set_article_url(args.commit, args.url, snapshot_text=snapshot)
    print("article url attached; the miner will serve this claim"
          + (" (with page snapshot)" if snapshot else ""))


def cmd_list(args):
    for onchain, rec in ClaimStore(args.store)._records.items():
        print(f"{onchain}\t{rec['brief_id']}\t{rec['target_outlet_id']}\t{rec['article_url']}")


def cmd_pull_reveals(args):
    """Pull reveals posted to the brief board (e.g. from the dashboard) into the local store."""
    import httpx

    headers = {"X-Reveals-Token": args.token} if args.token else {}
    resp = httpx.get(f"{args.url.rstrip('/')}/reveals", headers=headers, timeout=10.0)
    resp.raise_for_status()
    store = ClaimStore(args.store)
    imported = 0
    for reveal in resp.json():
        try:
            store.import_record(reveal)
            imported += 1
        except (KeyError, ValueError) as e:
            print(f"skip {reveal.get('onchain', '?')}: {e}")
    print(f"imported {imported} reveal(s) into {args.store}")


def build_parser():
    p = argparse.ArgumentParser(prog="herald-miner")
    p.add_argument("--store", default="claims.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("briefs").set_defaults(func=cmd_briefs)

    c = sub.add_parser("commit")
    c.add_argument("--brief", required=True)
    c.add_argument("--outlet", required=True)
    c.add_argument("--bond", type=int, default=0)
    c.add_argument("--version", type=int, default=1)
    c.add_argument("--netuid", type=int, default=69)
    c.add_argument("--network", default="finney")
    c.add_argument("--wallet-name", dest="wallet_name", default="default")
    c.add_argument("--hotkey", default="default")
    # Attribution evidence — hashed into the commitment, revealed + graded at claim.
    c.add_argument("--text-file", dest="text_file", default=None,
                   help="draft article text you expect to appear (level-2 text proof)")
    c.add_argument("--quote", default=None,
                   help="inline quote you supplied, instead of --text-file")
    c.add_argument("--author", default=None, help="expected byline (level 1, with --window)")
    c.add_argument("--window", default=None,
                   help="expected publish window START:END (YYYY-MM-DD), span <= 7 days")
    c.set_defaults(func=cmd_commit)

    cl = sub.add_parser("claim")
    cl.add_argument("--commit", required=True)
    cl.add_argument("--url", required=True)
    cl.add_argument("--snapshot-file", dest="snapshot_file", default=None,
                    help="attach this text file as the page snapshot instead of fetching")
    cl.add_argument("--no-snapshot", dest="no_snapshot", action="store_true",
                    help="claim without a page snapshot (validators fall back to their own fetch)")
    cl.set_defaults(func=cmd_claim)

    sub.add_parser("list").set_defaults(func=cmd_list)

    pr = sub.add_parser("pull-reveals")
    pr.add_argument("--url", required=True, help="brief board base URL")
    pr.add_argument("--token", default=None, help="HERALD_REVEALS_TOKEN (if the board requires it)")
    pr.set_defaults(func=cmd_pull_reveals)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
