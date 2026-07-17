import json
from types import SimpleNamespace

import pytest

from herald.registry import admin
from herald.registry.admin import build_parser
from herald.validator.news.registry_anchor import content_hash, encode_anchor
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

    key_file = tmp_path / "reg.key"
    key_file.write_text(priv)
    run(["sign", str(src), "--key-file", str(key_file), "--out", str(out)])
    signed = json.loads(out.read_text())
    assert verify(signed, pub) is True


def test_prepare_creates_new_unsigned_edition_without_changing_outlets(tmp_path):
    source = tmp_path / "v2.json"
    source.write_text(json.dumps({"version_id": 2, "outlets": [{"outlet_id": "a"}], "signature": "old"}))
    out = tmp_path / "v3.json"

    run(["prepare", str(source), "--out", str(out), "--version", "3"])

    prepared = json.loads(out.read_text())
    assert prepared == {"version_id": 3, "outlets": [{"outlet_id": "a"}]}


def test_gen_key_writes_private_to_mode_600_file(tmp_path):
    import os
    out_key = tmp_path / "k.key"
    run(["gen-key", "--out-key", str(out_key)])
    assert out_key.exists()
    assert (os.stat(out_key).st_mode & 0o777) == 0o600


def test_gen_key_refuses_to_overwrite_existing_private_key(tmp_path):
    out_key = tmp_path / "k.key"
    out_key.write_text("do-not-replace")

    with pytest.raises(SystemExit, match="already exists"):
        run(["gen-key", "--out-key", str(out_key)])

    assert out_key.read_text() == "do-not-replace"


def test_public_key_and_preflight_commands(tmp_path, capsys):
    priv, pub = generate_keypair()
    key_file = tmp_path / "reg.key"
    key_file.write_text(priv)
    run(["public-key", "--key-file", str(key_file)])
    assert capsys.readouterr().out.strip() == pub

    data = {"version_id": 7, "outlets": []}
    data["signature"] = admin.sign(data, priv)
    signed = tmp_path / "outlets.signed.json"
    signed.write_text(json.dumps(data))
    anchor = encode_anchor(7, content_hash(data), 5000)

    run(["preflight", str(signed), "--pubkey", pub, "--anchor", anchor])
    assert "signature: valid" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="anchor mismatch"):
        run(["preflight", str(signed), "--pubkey", pub,
             "--anchor", "HRLDREG|7|deadbeef|5000"])


def test_publish_anchor_verifies_and_uses_current_bittensor_api(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    data = {"version_id": 7, "outlets": []}
    data["signature"] = admin.sign(data, priv)
    signed = tmp_path / "outlets.signed.json"
    signed.write_text(json.dumps(data))

    calls = {}
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="5Authority"))

    class FakeSubtensor:
        def set_commitment(self, got_wallet, netuid, value, **kwargs):
            calls.update(wallet=got_wallet, netuid=netuid, value=value, kwargs=kwargs)

    monkeypatch.setattr(admin.bt, "Wallet", lambda name, hotkey: wallet)
    monkeypatch.setattr(admin.bt, "Subtensor", lambda network: FakeSubtensor())

    run([
        "publish-anchor", str(signed), "--pubkey", pub, "--effective-block", "5000",
        "--wallet-name", "cold", "--wallet-hotkey", "authority", "--netuid", "69",
        "--network", "test", "--yes",
    ])

    assert calls["wallet"] is wallet
    assert calls["netuid"] == 69
    assert calls["value"] == encode_anchor(7, content_hash(data), 5000)
    assert calls["kwargs"]["raise_error"] is True
    assert calls["kwargs"]["wait_for_finalization"] is True


def test_publish_anchor_requires_explicit_confirmation(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    data = {"version_id": 7, "outlets": []}
    data["signature"] = admin.sign(data, priv)
    signed = tmp_path / "outlets.signed.json"
    signed.write_text(json.dumps(data))
    monkeypatch.setattr(
        admin.bt, "Subtensor",
        lambda network: pytest.fail("must not connect without --yes"),
    )

    with pytest.raises(SystemExit, match="without --yes"):
        run([
            "publish-anchor", str(signed), "--pubkey", pub, "--effective-block", "5000",
            "--wallet-name", "cold", "--wallet-hotkey", "authority",
        ])


def test_verify_live_anchor_reads_authority_commitment(tmp_path, monkeypatch, capsys):
    priv, pub = generate_keypair()
    data = {"version_id": 7, "outlets": []}
    data["signature"] = admin.sign(data, priv)
    signed = tmp_path / "outlets.signed.json"
    signed.write_text(json.dumps(data))
    anchor = encode_anchor(7, content_hash(data), 5000)

    subtensor = object()
    monkeypatch.setattr(admin.bt, "Subtensor", lambda network: subtensor)
    monkeypatch.setattr(
        admin,
        "get_commitments_with_block",
        lambda got_subtensor, netuid: {"5Authority": (anchor, 5002)},
    )

    run([
        "verify-live-anchor", str(signed), "--pubkey", pub,
        "--authority", "5Authority", "--netuid", "69", "--network", "test",
    ])

    output = capsys.readouterr().out
    assert "live anchor: valid" in output
    assert "commit block: 5002" in output
