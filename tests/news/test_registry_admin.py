import json

from herald.registry.admin import build_parser
from herald.validator.news.registry_signing import generate_keypair, verify


def run(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


def test_add_sign_verify_flow(tmp_path):
    priv, pub = generate_keypair()
    src = tmp_path / "outlets.json"
    src.write_text(json.dumps({"version_id": 1, "outlets": []}))
    out = tmp_path / "outlets.signed.json"

    run(["add", str(src), "--outlet-id", "nyt", "--tier", "1", "--domains", "nytimes.com"])
    data = json.loads(src.read_text())
    assert data["version_id"] == 2 and data["outlets"][0]["status"] == "probation"

    run(["sign", str(src), "--key", priv, "--out", str(out)])
    signed = json.loads(out.read_text())
    assert verify(signed, pub) is True
