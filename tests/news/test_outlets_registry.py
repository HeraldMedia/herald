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
for fname in ("tier1_fixture.json", "tier2_fixture.json", "tier3_fixture.json"):
    _FIXTURE.update({f["outlet_id"]: f for f in json.load(open(_DIR / fname))})
_OUTLETS = {o.outlet_id: o for o in _REGISTRY.outlets}
_IDS = sorted(_OUTLETS)

# Generic synthetic "obviously editorial" paths, independent of any outlet's own fixture data.
# A paid_pattern that matches ALL of these is a match-everything pattern (e.g. bare ".*") that
# would reject every honest placement on that outlet -- catchable even when the outlet's real
# fixture happens to have no editorial_url on file (the gap that let one through undetected).
_GENERIC_EDITORIAL_PATHS = [
    "/2026/07/14/markets/fed-rate-decision-explainer",
    "/news/local-council-approves-new-budget-plan",
    "/world/politics/election-results-analysis",
    "/technology/startup-raises-series-a-funding",
    "/a1b2c3d4-story-slug",
]

BENIGN = ("The minister said on Monday that the new policy would take effect next year, "
          "according to officials familiar with the plans. Reporters covered the announcement.")


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _path(url):
    return urlsplit(url).path or "/"


def test_registry_has_all_three_tiers():
    assert len(_REGISTRY.outlets) == len(_IDS) > 200
    tiers = {o.tier for o in _REGISTRY.outlets}
    assert tiers == {1, 2, 3}


@pytest.mark.parametrize("oid", _IDS)
def test_every_outlet_has_a_fixture_editorial_url(oid):
    # A paid_pattern can only be false-positive-checked against a real editorial URL. An outlet
    # with patterns but no fixture URL silently skips that check -- exactly how a bare ".*"
    # pattern (matches every path) shipped undetected for one outlet before this test existed.
    f = _FIXTURE.get(oid)
    assert f and f.get("editorial_url"), f"{oid}: no editorial_url on file -- paid_patterns are unverifiable"


@pytest.mark.parametrize("oid", _IDS)
def test_no_paid_pattern_matches_everything(oid):
    # Independent of fixture data: a pattern matching ALL generic editorial-shaped paths would
    # reject every honest placement on this outlet. Catches this even if the real fixture URL
    # happened to be missing (defense in depth alongside the fixture-presence test above).
    for pat in _OUTLETS[oid].paid_patterns:
        matches_all = all(re.search(pat, p, re.I) for p in _GENERIC_EDITORIAL_PATHS)
        assert not matches_all, f"{oid}: pattern {pat!r} matches every generic editorial path"


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
def test_outlet_has_a_paid_detector_when_paid_content_is_confirmed(oid):
    # An outlet with a CONFIRMED paid/sponsored example must have some way to catch it (either
    # its own pattern/marker, or the generic global fallback in is_paid). An outlet where research
    # found no evidence any paid program exists at all relies on the generic floor alone -- that's
    # an evidence-backed conclusion, not a gap, so it's not required to carry its own detector.
    o = _OUTLETS[oid]
    f = _FIXTURE.get(oid)
    ex = (f.get("paid_example_url") or "") if f else ""
    if not ex:
        pytest.skip(f"{oid}: no confirmed paid example -- research found no evidence of a paid program")
    assert o.paid_patterns or o.paid_markers, f"{oid}: has a confirmed sponsored example but no detector"


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
