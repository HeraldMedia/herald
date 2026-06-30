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

    def matches(self, url: str) -> bool:
        if host_of(url) not in self.domains:
            return False
        if not self.section_patterns:
            return True
        path = urlsplit(url).path or "/"
        return any(re.search(p, path) for p in self.section_patterns)


class OutletRegistry:
    def __init__(self, outlets: List[Outlet], version_id: int):
        self.outlets = outlets
        self.version_id = version_id

    @classmethod
    def from_dict(cls, data: dict) -> "OutletRegistry":
        outlets = [
            Outlet(
                outlet_id=o["outlet_id"],
                tier=int(o["tier"]),
                domains=list(o["domains"]),
                section_patterns=list(o.get("section_patterns", [])),
            )
            for o in data.get("outlets", [])
        ]
        return cls(outlets, int(data.get("version_id", 0)))

    @classmethod
    def from_json_file(cls, path: str) -> "OutletRegistry":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def lookup(self, url: str) -> Optional[Outlet]:
        for outlet in self.outlets:
            if outlet.matches(url):
                return outlet
        return None


def load_registry() -> OutletRegistry:
    path = os.getenv("HERALD_REGISTRY_PATH", str(_SEED_PATH))
    return OutletRegistry.from_json_file(path)
