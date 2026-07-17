"""Operator CLI: manage and sign the outlet registry (the trust anchor)."""

import argparse
import json
import os

import bittensor as bt

from herald.validator.news.chain import get_commitments_with_block
from herald.validator.news.registry_anchor import (
    content_hash,
    encode_anchor,
    parse_anchor,
    verify_anchor,
)
from herald.validator.news.registry_signing import (
    generate_keypair,
    public_key_from_private,
    sign,
    verify,
)


def cmd_genkey(args):
    priv, pub = generate_keypair()
    try:
        fd = os.open(args.out_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f"private-key file already exists: {args.out_key}") from None
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


def cmd_public_key(args):
    with open(args.key_file, "r", encoding="utf-8") as f:
        private_key = f.read().strip()
    try:
        print(public_key_from_private(private_key))
    except ValueError:
        raise SystemExit("private key must be exactly 32 bytes encoded as hexadecimal") from None


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _preflight(infile, pubkey, anchor):
    data = _load(infile)
    try:
        signature_valid = verify(data, pubkey)
    except ValueError:
        signature_valid = False
    if not signature_valid:
        raise SystemExit("registry signature invalid")
    if not verify_anchor(data, anchor):
        raise SystemExit("registry anchor mismatch")
    parsed = parse_anchor(anchor)
    return data, parsed


def cmd_preflight(args):
    data, parsed = _preflight(args.infile, args.pubkey, args.anchor)
    print("signature: valid")
    print("anchor: valid")
    print(f"version: {data['version_id']}")
    print(f"content hash: {content_hash(data)}")
    print(f"effective block: {parsed['effective_block']}")


def cmd_add(args):
    data = _load(args.infile)
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
    data = _load(args.infile)
    data["signature"] = sign(data, _read_key(args))
    _write(args.out, data)
    print(f"signed version {data.get('version_id')} -> {args.out}")


def cmd_prepare(args):
    data = _load(args.infile)
    current = int(data.get("version_id", 0))
    version = args.version if args.version is not None else current + 1
    if version != current + 1:
        raise SystemExit(f"new version must be exactly {current + 1}")
    data["version_id"] = version
    data.pop("signature", None)
    _write(args.out, data)
    print(f"prepared unsigned version {version} -> {args.out}")


def cmd_verify(args):
    data = _load(args.infile)
    print("valid" if verify(data, args.pubkey) else "INVALID")


def cmd_anchor(args):
    data = _load(args.infile)
    anchor = encode_anchor(data["version_id"], content_hash(data), args.effective_block)
    print(anchor)
    print("commit this on chain from the authority hotkey:")
    print("  python -m herald.registry.admin publish-anchor <signed-registry> \\")
    print("    --pubkey <public-hex> --effective-block "
          f"{args.effective_block} --wallet-name <wallet> --wallet-hotkey <authority> --yes")


def cmd_publish_anchor(args):
    data = _load(args.infile)
    anchor = encode_anchor(data["version_id"], content_hash(data), args.effective_block)
    _preflight(args.infile, args.pubkey, anchor)
    print(f"verified anchor: {anchor}")
    if not args.yes:
        raise SystemExit("refusing on-chain write without --yes")

    wallet = bt.Wallet(name=args.wallet_name, hotkey=args.wallet_hotkey)
    subtensor = bt.Subtensor(network=args.network)
    subtensor.set_commitment(
        wallet,
        args.netuid,
        anchor,
        raise_error=True,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    print(f"registry anchor finalized on netuid {args.netuid} from {wallet.hotkey.ss58_address}")


def cmd_verify_live_anchor(args):
    subtensor = bt.Subtensor(network=args.network)
    record = get_commitments_with_block(subtensor, args.netuid).get(args.authority)
    if record is None:
        raise SystemExit("registry authority has no on-chain commitment")
    anchor, commit_block = record
    data, parsed = _preflight(args.infile, args.pubkey, anchor)
    print("signature: valid")
    print("live anchor: valid")
    print(f"version: {data['version_id']}")
    print(f"content hash: {content_hash(data)}")
    print(f"effective block: {parsed['effective_block']}")
    print(f"commit block: {commit_block}")


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic: a crash never truncates the signed registry


def build_parser():
    p = argparse.ArgumentParser(prog="herald-registry")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen-key")
    g.add_argument("--out-key", dest="out_key", default="herald-registry.ed25519.key")
    g.set_defaults(func=cmd_genkey)

    pk = sub.add_parser("public-key")
    pk.add_argument("--key-file", required=True)
    pk.set_defaults(func=cmd_public_key)

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

    prep = sub.add_parser("prepare")
    prep.add_argument("infile")
    prep.add_argument("--out", required=True)
    prep.add_argument("--version", type=int, default=None)
    prep.set_defaults(func=cmd_prepare)

    v = sub.add_parser("verify")
    v.add_argument("infile")
    v.add_argument("--pubkey", required=True)
    v.set_defaults(func=cmd_verify)

    an = sub.add_parser("anchor")
    an.add_argument("infile")
    an.add_argument("--effective-block", dest="effective_block", type=int, required=True)
    an.set_defaults(func=cmd_anchor)

    pf = sub.add_parser("preflight")
    pf.add_argument("infile")
    pf.add_argument("--pubkey", required=True)
    pf.add_argument("--anchor", required=True)
    pf.set_defaults(func=cmd_preflight)

    publish = sub.add_parser("publish-anchor")
    publish.add_argument("infile")
    publish.add_argument("--pubkey", required=True)
    publish.add_argument("--effective-block", dest="effective_block", type=int, required=True)
    publish.add_argument("--wallet-name", required=True)
    publish.add_argument("--wallet-hotkey", required=True)
    publish.add_argument("--netuid", type=int, default=69)
    publish.add_argument("--network", default="finney",
                         help="Bittensor network name or websocket endpoint")
    publish.add_argument("--yes", action="store_true",
                         help="confirm the finalized on-chain commitment write")
    publish.set_defaults(func=cmd_publish_anchor)

    live = sub.add_parser("verify-live-anchor")
    live.add_argument("infile")
    live.add_argument("--pubkey", required=True)
    live.add_argument("--authority", required=True)
    live.add_argument("--netuid", type=int, default=69)
    live.add_argument("--network", default="finney",
                      help="Bittensor network name or websocket endpoint")
    live.set_defaults(func=cmd_verify_live_anchor)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
