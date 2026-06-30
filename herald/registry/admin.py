"""Operator CLI: manage and sign the outlet registry (the trust anchor)."""

import argparse
import json
import os

from herald.validator.news.registry_anchor import content_hash, encode_anchor
from herald.validator.news.registry_signing import generate_keypair, sign, verify


def cmd_genkey(args):
    priv, pub = generate_keypair()
    fd = os.open(args.out_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(priv)
    print(f"public:  {pub}")
    print(f"private key written to {args.out_key} (mode 600) — keep it offline")


def _read_key(args) -> str:
    if args.key_file:
        with open(args.key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = os.getenv("HERALD_REGISTRY_PRIVKEY")
    if not key:
        raise SystemExit("provide the signing key via --key-file or HERALD_REGISTRY_PRIVKEY")
    return key


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
    data["signature"] = sign(data, _read_key(args))
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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic: a crash never truncates the signed registry


def build_parser():
    p = argparse.ArgumentParser(prog="herald-registry")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen-key")
    g.add_argument("--out-key", dest="out_key", default="herald_registry.key")
    g.set_defaults(func=cmd_genkey)

    a = sub.add_parser("add")
    a.add_argument("infile")
    a.add_argument("--outlet-id", dest="outlet_id", required=True)
    a.add_argument("--tier", type=int, required=True)
    a.add_argument("--domains", nargs="+", required=True)
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("sign")
    s.add_argument("infile")
    s.add_argument("--key-file", dest="key_file", default=None)
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
