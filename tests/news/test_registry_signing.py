from herald.validator.news.registry_signing import (
    canonical_bytes, generate_keypair, sign, verify,
)

DATA = {"version_id": 7, "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["nytimes.com"]}]}


def test_sign_then_verify():
    priv, pub = generate_keypair()
    signed = {**DATA, "signature": sign(DATA, priv)}
    assert verify(signed, pub) is True


def test_tamper_detected():
    priv, pub = generate_keypair()
    signed = {**DATA, "signature": sign(DATA, priv)}
    signed["version_id"] = 8
    assert verify(signed, pub) is False


def test_wrong_key_rejected():
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    signed = {**DATA, "signature": sign(DATA, priv)}
    assert verify(signed, other_pub) is False


def test_missing_signature_rejected():
    _, pub = generate_keypair()
    assert verify(DATA, pub) is False


def test_malformed_signature_rejected_not_raised():
    # a registry file with a non-string / non-hex signature must verify-False, not crash
    # the validator scoring loop at load time.
    _, pub = generate_keypair()
    for bad in (None, 123, ["x"], {"a": 1}, "", "abc", "zz"):
        assert verify({**DATA, "signature": bad}, pub) is False


def test_canonical_bytes_ignores_signature_and_key_order():
    a = {"version_id": 1, "outlets": [], "signature": "xx"}
    b = {"outlets": [], "version_id": 1}
    assert canonical_bytes(a) == canonical_bytes(b)
