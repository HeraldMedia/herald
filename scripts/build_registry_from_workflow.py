#!/usr/bin/env python3
"""Assemble the Tier-1 outlet registry from the research+verify workflow output.

Usage: python scripts/build_registry_from_workflow.py <workflow.output.json> [out_dir]

Merges each outlet's research proposal with its adversarial verdict (corrected_* fields win),
validates the paid patterns against the outlet's real editorial vs sponsored URLs, and writes
the registry JSON plus a test fixture of (editorial_url, paid_example_url) per outlet.
"""
import json
import re
import sys
from urllib.parse import urlsplit


def _path(url: str) -> str:
    try:
        return urlsplit(url or "").path or "/"
    except Exception:
        return "/"


def _norm_domains(domains):
    out = []
    for d in domains or []:
        d = str(d).strip().lower()
        if "://" in d:
            d = urlsplit(d).hostname or d
        d = d.split("/")[0].strip()
        if d and d not in out:
            out.append(d)
    return out


def merge(entry: dict, tier: int = 1) -> dict:
    r = entry.get("research") or {}
    v = entry.get("verdict") or {}
    oid = entry["outlet_id"]  # canonical id from our input list

    def pick(corrected_key, research_key):
        cv = v.get(corrected_key)
        return cv if cv else r.get(research_key, [])

    domains = _norm_domains(pick("corrected_domains", "domains"))
    section_patterns = pick("corrected_section_patterns", "section_patterns")
    paid_patterns = pick("corrected_paid_url_patterns", "paid_url_patterns")
    paid_markers = pick("corrected_paid_markers", "paid_markers")
    fetch = v.get("corrected_fetch") or r.get("fetch") or "direct"

    return {
        "outlet_id": oid,
        "tier": tier,
        "domains": domains,
        "section_patterns": list(section_patterns),
        "fetch": fetch,
        "paid_patterns": list(paid_patterns),
        "paid_markers": list(paid_markers),
        "_editorial_url": r.get("editorial_article_url", ""),
        "_paid_example_url": r.get("paid_example_url", ""),
        "_confidence": (v.get("final_confidence") or r.get("confidence") or "unknown"),
        "_has_verdict": bool(v),
    }


def _host(url: str) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except Exception:
        return ""


def validate(o: dict):
    """Problems (must-fix) vs notes (acceptable). The critical direction is false-positives:
    a paid pattern must NEVER match a real editorial URL. A paid example that lives on an
    unlisted host (rejected as outlet_not_listed anyway) or an editorial-looking URL caught only
    by on-page markers is NOT a URL-pattern gap."""
    problems, notes = [], []
    ed_path = _path(o["_editorial_url"])
    for pat in o["paid_patterns"]:
        try:
            re.compile(pat)
        except re.error as e:
            problems.append(f"BAD-REGEX {pat!r}: {e}")
            continue
        if o["_editorial_url"] and re.search(pat, ed_path, re.I):
            problems.append(f"FALSE-POSITIVE: /{pat}/ matches editorial path {ed_path}")
    if not o["domains"]:
        problems.append("NO DOMAINS")
    if o["_editorial_url"] and _host(o["_editorial_url"]) not in o["domains"]:
        problems.append(f"editorial host {_host(o['_editorial_url'])} not in domains {o['domains']}")

    ex = o["_paid_example_url"]
    if ex:
        ex_host, ex_path = _host(ex), _path(ex)
        caught = any(_safe_search(p, ex_path) for p in o["paid_patterns"])
        if ex_host not in o["domains"]:
            notes.append(f"paid example on unlisted host {ex_host} (rejected as outlet_not_listed)")
        elif not caught and o["paid_markers"]:
            notes.append("paid example at editorial-shaped URL -> detected by on-page markers, not URL")
        elif not caught:
            problems.append(f"UNDETECTABLE: paid example {ex_path} matched by no URL pattern and no markers")
    return problems, notes


def _safe_search(pat, s):
    try:
        return bool(re.search(pat, s, re.I))
    except re.error:
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--out", default="herald/validator/news/outlets.tier1.json")
    ap.add_argument("--fixture", default="tests/news/tier1_fixture.json")
    a = ap.parse_args()
    data = json.load(open(a.src))
    if isinstance(data, dict) and "result" in data:
        data = data["result"]  # unwrap the workflow task envelope
    merged = [merge(e, a.tier) for e in data if isinstance(e, dict) and e.get("research")]

    print(f"outlets: {len(merged)}  with_verdict: {sum(o['_has_verdict'] for o in merged)}")
    fetch_kinds = {}
    for o in merged:
        k = o["fetch"].split(":")[0]
        fetch_kinds[k] = fetch_kinds.get(k, 0) + 1
    print("fetch:", fetch_kinds)

    any_problem = False
    for o in sorted(merged, key=lambda x: x["outlet_id"]):
        probs, notes = validate(o)
        tag = "!!" if probs else "  "
        print(f"{tag} {o['outlet_id']:<24} dom={len(o['domains'])} paid={len(o['paid_patterns'])} "
              f"mark={len(o['paid_markers'])} fetch={o['fetch']:<10} conf={o['_confidence']}")
        for p in probs:
            any_problem = True
            print(f"      !! {p}")
        for n in notes:
            print(f"       - {n}")

    # registry: strip the _private test fields
    registry = {"version_id": 1, "outlets": [
        {k: v for k, v in o.items() if not k.startswith("_")}
        for o in sorted(merged, key=lambda x: x["outlet_id"])
    ]}
    with open(a.out, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"\nwrote {a.out}  ({len(registry['outlets'])} outlets)")

    fixture = [{"outlet_id": o["outlet_id"], "editorial_url": o["_editorial_url"],
                "paid_example_url": o["_paid_example_url"], "paid_patterns": o["paid_patterns"],
                "domains": o["domains"]} for o in merged]
    with open(a.fixture, "w") as f:
        json.dump(fixture, f, indent=2)
    print(f"wrote {a.fixture}")
    print("VALIDATION:", "PROBLEMS FOUND" if any_problem else "clean")


if __name__ == "__main__":
    main()
