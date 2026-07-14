"""The shipped outlet registry must never let a paid pattern reject a real editorial URL, every
outlet must have a working paid-content detector, and no two outlets may claim overlapping domains
(registry.lookup is first-match-wins, so a collision silently makes the losing outlet_id
unreachable -> every honest claim against it rejects as outlet_mismatch). Data verified live per
outlet; fixtures carry each outlet's real editorial + sponsored example URL."""
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from herald.validator.news.real_news import is_paid
from herald.validator.news.registry import OutletRegistry

_DIR = Path(__file__).resolve().parent
_NEWS_DIR = _DIR.parent.parent / "herald/validator/news"
_REGISTRY = OutletRegistry.from_json_file(str(_NEWS_DIR / "outlets.json"))
_FIXTURE = {}
for fname in ("tier1_fixture.json", "tier2_fixture.json"):
    _FIXTURE.update({f["outlet_id"]: f for f in json.load(open(_DIR / fname))})
_OUTLETS = {o.outlet_id: o for o in _REGISTRY.outlets}
_IDS = sorted(_OUTLETS)

BENIGN = ("The minister said on Monday that the new policy would take effect next year, "
          "according to officials familiar with the plans. Reporters covered the announcement.")


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _path(url):
    return urlsplit(url).path or "/"


def test_registry_has_tier1_and_tier2_outlets():
    assert len(_REGISTRY.outlets) == len(_IDS) > 100
    tiers = {o.tier for o in _REGISTRY.outlets}
    assert tiers == {1, 2}


def test_no_two_outlets_claim_the_same_domain():
    # registry.lookup() is a linear first-match scan: two outlets sharing an exact host makes
    # whichever sorts later permanently unreachable (every claim against it -> outlet_mismatch).
    owner = {}
    collisions = []
    for o in _REGISTRY.outlets:
        for d in o.domains:
            if d in owner and owner[d] != o.outlet_id:
                collisions.append((d, owner[d], o.outlet_id))
            owner[d] = o.outlet_id
    assert collisions == []


@pytest.mark.parametrize("oid", _IDS)
def test_editorial_url_resolves_to_its_own_outlet(oid):
    # Full end-to-end lookup (not just "is this host in my domains") -- catches a domain claimed
    # by an earlier outlet in the list silently swallowing this one's traffic.
    f = _FIXTURE.get(oid)
    ed = f.get("editorial_url") if f else None
    if not ed:
        pytest.skip(f"{oid}: no editorial url in fixture")
    hit = _REGISTRY.lookup(ed)
    assert hit is not None and hit.outlet_id == oid, f"{oid}: editorial url resolved to {hit.outlet_id if hit else None}"


@pytest.mark.parametrize("oid", _IDS)
def test_paid_patterns_compile_and_are_not_redos(oid):
    for pat in _OUTLETS[oid].paid_patterns:
        re.compile(pat)  # raises on bad regex
        t = time.perf_counter()
        re.search(pat, "/" + "abc123-" * 300, re.I)  # ~2KB adversarial path
        assert time.perf_counter() - t < 0.1, f"{oid}: slow pattern {pat!r}"


@pytest.mark.parametrize("oid", _IDS)
def test_editorial_url_is_never_flagged_paid(oid):
    # THE critical guarantee: a real editorial article must not be rejected as paid, by URL
    # pattern OR by an over-broad on-page marker hitting normal prose.
    f = _FIXTURE.get(oid)
    ed = f.get("editorial_url") if f else None
    if not ed:
        pytest.skip(f"{oid}: no editorial url in fixture")
    assert _host(ed) in _OUTLETS[oid].domains, f"{oid}: editorial host not in domains"
    paid, reason = is_paid(ed, BENIGN, None, outlet=_OUTLETS[oid])
    assert paid is False, f"{oid}: editorial wrongly flagged paid ({reason})"


@pytest.mark.parametrize("oid", _IDS)
def test_outlet_has_a_paid_detector(oid):
    o = _OUTLETS[oid]
    assert o.paid_patterns or o.paid_markers, f"{oid}: no paid-content detector at all"


@pytest.mark.parametrize("oid", _IDS)
def test_sponsored_example_is_caught_when_url_detectable(oid):
    # Where the sponsored example lives on a listed domain and any URL pattern matches its path,
    # is_paid must catch it. (Unlisted-host examples are rejected as outlet_not_listed; editorial-
    # shaped sponsored URLs are covered by markers against fetched text, not by this URL check.)
    f = _FIXTURE.get(oid)
    ex = (f.get("paid_example_url") or "") if f else ""
    o = _OUTLETS[oid]
    if not ex or _host(ex) not in o.domains:
        pytest.skip(f"{oid}: no on-domain sponsored example")
    if any(re.search(p, _path(ex), re.I) for p in o.paid_patterns):
        assert is_paid(ex, "", None, outlet=o)[0] is True, f"{oid}: on-domain sponsored URL not caught"
    else:
        pytest.skip(f"{oid}: sponsored example is editorial-shaped (marker-detected)")
