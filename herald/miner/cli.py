"""Operator CLI: read briefs, commit to an outlet, attach the published URL."""

import argparse

import bittensor as bt

from herald.miner.claim_store import ClaimStore
from herald.miner.commit import submit_commitment
from herald.validator.utils.briefs import get_briefs


def cmd_briefs(args):
    for b in get_briefs():
        print(f"{b['id']}\t{b.get('title', '')}\t{b.get('start_date')}..{b.get('end_date')}")


def cmd_commit(args):
    wallet = bt.wallet(name=args.wallet_name, hotkey=args.hotkey)
    subtensor = bt.subtensor(network=args.network)
    onchain = submit_commitment(
        subtensor, wallet, args.netuid, ClaimStore(args.store),
        brief_id=args.brief, target_outlet_id=args.outlet,
        bond_atto=args.bond, version_id=args.version,
    )
    print(f"committed: {onchain}")


def cmd_claim(args):
    ClaimStore(args.store).set_article_url(args.commit, args.url)
    print("article url attached; the miner will serve this claim")


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
    c.set_defaults(func=cmd_commit)

    cl = sub.add_parser("claim")
    cl.add_argument("--commit", required=True)
    cl.add_argument("--url", required=True)
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
