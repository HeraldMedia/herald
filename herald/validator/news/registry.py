"""Outlet registry: maps an article URL to an approved outlet and its tier."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from .url import host_of

_SEED_PATH = Path(__file__).parent / "outlets.seed.json"


@dataclass
class Outlet:
    outlet_id: str
    tier: int
    domains: List[str]
    section_patterns: List[str] = field(default_factory=list)
    # How validators fetch/verify this outlet (travels in the SIGNED registry, so the whole fleet
    # agrees): "direct" = plain HTTP (default), "proxy[:profile]" = ScrapingBee using classic,
    # js, premium, premium_js, or stealth mode, "api:<name>" = authoritative publisher metadata +
    # the miner snapshot anchored to it, and "disabled" = listed but ineligible until restored.
    fetch: str = "direct"
    # This outlet's OWN branded/sponsored/contributor programs — the main Tier-1 cheat vector.
    # paid_patterns: regexes (re.search, case-insensitive) vs the URL path that mark PAID content
    # (e.g. Forbes "brandvoice"); paid_markers: on-page disclosure labels. Both travel signed.
    paid_patterns: List[str] = field(default_factory=list)
    paid_markers: List[str] = field(default_factory=list)

    def matches(self, url: str) -> bool:
        host = host_of(url)
        # Accept the www. variant of a listed domain — still an exact-host match (no suffix
        # matching), so evil-nytimes.com / nytimes.com.evil.com stay rejected.
        bare = host[4:] if host.startswith("www.") else host
        if host not in self.domains and bare not in self.domains:
            return False
        if not self.section_patterns:
            return True
        path = urlsplit(url).path or "/"
        return any(re.search(p, path) for p in self.section_patterns)


class OutletRegistry:
    def __init__(self, outlets: List[Outlet], version_id: int, content_hash: str = ""):
        self.outlets = outlets
        self.version_id = version_id
        self.content_hash = content_hash

    @classmethod
    def from_dict(cls, data: dict) -> "OutletRegistry":
        outlets = [
            Outlet(
                outlet_id=o["outlet_id"],
                tier=int(o["tier"]),
                domains=list(o["domains"]),
                section_patterns=list(o.get("section_patterns", [])),
                fetch=str(o.get("fetch", "direct")),
                paid_patterns=list(o.get("paid_patterns", [])),
                paid_markers=list(o.get("paid_markers", [])),
            )
            for o in data.get("outlets", [])
        ]
        from .registry_anchor import content_hash
        return cls(outlets, int(data.get("version_id", 0)), content_hash(data))

    @classmethod
    def from_json_file(cls, path: str) -> "OutletRegistry":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def lookup(self, url: str) -> Optional[Outlet]:
        for outlet in self.outlets:
            if outlet.matches(url):
                return outlet
        return None


def load_registry(anchor_value: str = None, require_anchor: bool = False,
                  current_block: int = None, network: str = None,
                  netuid: int = None) -> OutletRegistry:
    path = os.getenv("HERALD_REGISTRY_PATH", str(_SEED_PATH))
    endpoint = os.getenv("HERALD_REGISTRY_ENDPOINT")
    cache_path = os.getenv("HERALD_REGISTRY_CACHE_PATH", path + ".verified-cache")
    candidates = []
    if endpoint:
        try:
            import httpx
            params = ({"network": network, "netuid": int(netuid)}
                      if network is not None and netuid is not None else None)
            response = httpx.get(endpoint.rstrip("/") + "/api/v3/validator/registry",
                                 params=params, timeout=10.0)
            response.raise_for_status()
            candidates.append((response.json(), True))
        except Exception:
            pass
    for candidate in (cache_path, path):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                candidates.append((json.load(f), False))
        except (OSError, json.JSONDecodeError):
            pass
    if not candidates:
        raise ValueError("no outlet registry edition available")
    pubkey = os.getenv("HERALD_REGISTRY_PUBKEY")
    parsed_anchor = None
    pre_effective = False
    if anchor_value:
        from .registry_anchor import parse_anchor
        parsed_anchor = parse_anchor(anchor_value)
        if parsed_anchor is None:
            raise ValueError("outlet registry on-chain anchor malformed")
        pre_effective = (current_block is not None
                         and current_block < parsed_anchor["effective_block"])
    last_error = "outlet registry verification failed"
    for data, remote in candidates:
        try:
            if pubkey:
                from .registry_signing import verify
                if not verify(data, pubkey):
                    raise ValueError("outlet registry signature verification failed")
            elif os.getenv("HERALD_REQUIRE_SIGNED_REGISTRY", "false").lower() == "true":
                raise ValueError("HERALD_REGISTRY_PUBKEY required but not set")
            else:
                import bittensor as bt
                bt.logging.warning("Loading UNSIGNED outlet registry; set HERALD_REGISTRY_PUBKEY in production")
            if require_anchor and not anchor_value:
                raise ValueError("outlet registry on-chain anchor required but missing")
            if anchor_value:
                from .registry_anchor import verify_anchor
                if pre_effective and int(data.get("version_id", 0)) != parsed_anchor["version_id"] - 1:
                    raise ValueError("future registry anchor does not match the current edition")
                if not pre_effective and not verify_anchor(data, anchor_value):
                    raise ValueError("outlet registry on-chain anchor mismatch")
            if remote:
                tmp = cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, cache_path)
            return OutletRegistry.from_dict(data)
        except (OSError, TypeError, ValueError) as exc:
            last_error = str(exc)
    raise ValueError(last_error)
