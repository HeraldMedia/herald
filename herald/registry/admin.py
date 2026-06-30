"""Operator CLI: manage and sign the outlet registry (the trust anchor)."""

import argparse
import json

from herald.validator.news.registry_anchor import content_hash, encode_anchor
from herald.validator.news.registry_signing import generate_keypair, sign, verify


def cmd_genkey(args):
    priv, pub = generate_keypair()
    print(f"private: {priv}")
    print(f"public:  {pub}")


def cmd_add(args):
    with open(args.infile, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("outlets", []).append({
        "outlet_id": args.outlet_id,
        "tier": args.tier,
        "domains": args.domains,
        "status": "probation",
    })
    data["version_id"] = data.get("version_id", 0) + 1
    data.pop("signature", None)
    _write(args.infile, data)
    print(f"added {args.outlet_id} (probation); version -> {data['version_id']}; re-sign before publishing")


def cmd_sign(args):
    with open(args.infile, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["signature"] = sign(data, args.key)
    _write(args.out, data)
    print(f"signed version {data.get('version_id')} -> {args.out}")


def cmd_verify(args):
    with open(args.infile, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("valid" if verify(data, args.pubkey) else "INVALID")


def cmd_anchor(args):
    with open(args.infile, "r", encoding="utf-8") as f:
        data = json.load(f)
    anchor = encode_anchor(data["version_id"], content_hash(data), args.effective_block)
    print(anchor)
    print("commit this on chain from the authority hotkey:")
    print(f"  btcli ... or subtensor.commit(wallet, netuid=69, data=\"{anchor}\")")


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def build_parser():
    p = argparse.ArgumentParser(prog="herald-registry")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gen-key").set_defaults(func=cmd_genkey)

    a = sub.add_parser("add")
    a.add_argument("infile")
    a.add_argument("--outlet-id", dest="outlet_id", required=True)
    a.add_argument("--tier", type=int, required=True)
    a.add_argument("--domains", nargs="+", required=True)
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("sign")
    s.add_argument("infile")
    s.add_argument("--key", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_sign)

    v = sub.add_parser("verify")
    v.add_argument("infile")
    v.add_argument("--pubkey", required=True)
    v.set_defaults(func=cmd_verify)

    an = sub.add_parser("anchor")
    an.add_argument("infile")
    an.add_argument("--effective-block", dest="effective_block", type=int, required=True)
    an.set_defaults(func=cmd_anchor)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
