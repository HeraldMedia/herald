"""File-backed stores for briefs and verified-article results."""

import json
import os
import secrets
from typing import List, Optional


_MAX_RESULTS = int(os.getenv("HERALD_MAX_RESULTS", "100000"))


def _load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic: a crash never leaves a truncated store


class BriefStore:
    def __init__(self, path: str):
        self.path = path
        self._briefs = _load(path, {})

    def create(self, data: dict) -> dict:
        bid = data.get("id") or secrets.token_hex(8)
        brief = {**data, "id": bid, "status": "draft", "funded": False}
        self._briefs[bid] = brief
        _save(self.path, self._briefs)
        return brief

    def fund(self, brief_id: str) -> dict:
        brief = self._briefs[brief_id]
        brief["funded"] = True
        brief["status"] = "open"
        _save(self.path, self._briefs)
        return brief

    def set_boost(self, brief_id: str, boost: float) -> Optional[dict]:
        brief = self._briefs.get(brief_id)
        if brief is None:
            return None
        brief["boost"] = boost  # carried into the signed validator feed; the validator clamps it
        _save(self.path, self._briefs)
        return brief

    def get(self, brief_id: str) -> Optional[dict]:
        return self._briefs.get(brief_id)

    def open_briefs(self) -> List[dict]:
        return [b for b in self._briefs.values() if b["funded"] and b["status"] == "open"]

    def all_briefs(self) -> List[dict]:
        return list(self._briefs.values())


class ResultStore:
    def __init__(self, path: str, max_items: int = _MAX_RESULTS):
        self.path = path
        self.max_items = max_items
        self._items = _load(path, [])

    def add(self, item: dict):
        article_id = item.get("article_id")
        if article_id is not None:
            for i, existing in enumerate(self._items):
                if existing.get("article_id") == article_id:
                    self._items[i] = item
                    _save(self.path, self._items)
                    return
        self._items.append(item)
        while len(self._items) > self.max_items:
            self._items.pop(0)  # bound disk growth; evict oldest
        _save(self.path, self._items)

    def articles(self) -> List[dict]:
        return list(self._items)

    def leaderboard(self) -> List[dict]:
        agg = {}
        for item in self._items:
            hotkey = item.get("hotkey")
            if not hotkey:
                continue  # unattributable result: skip, don't crash the public page
            row = agg.setdefault(hotkey, {"hotkey": hotkey, "articles": 0, "total_usd": 0.0})
            row["articles"] += 1
            row["total_usd"] += item.get("usd", 0.0)
        return sorted(agg.values(), key=lambda r: -r["total_usd"])


class RegistryStore:
    """A working DRAFT of the outlet registry the operator edits before signing it offline.

    Seeded from the current live (signed) registry; the draft is unsigned by construction —
    it carries no signature and bumps version_id once over live. The Ed25519 signing key NEVER
    touches this service: the operator exports the draft and signs it with the offline
    `herald-registry` CLI, then publishes the signed file. None => no draft (live is current).
    """

    def __init__(self, path: str, live_loader):
        self.path = path
        self._live_loader = live_loader
        self._draft = _load(path, None)

    def live(self) -> dict:
        return self._live_loader()

    def draft(self) -> Optional[dict]:
        return self._draft

    def _ensure(self) -> dict:
        if self._draft is None:
            live = self._live_loader()
            self._draft = {
                "version_id": int(live.get("version_id", 0)) + 1,
                "outlets": [dict(o) for o in live.get("outlets", [])],
            }
        return self._draft

    def add_outlet(self, outlet_id: str, tier: int, domains: list) -> dict:
        d = self._ensure()
        d["outlets"].append({"outlet_id": outlet_id, "tier": int(tier),
                             "domains": list(domains), "status": "probation"})
        _save(self.path, d)
        return d

    def set_status(self, outlet_id: str, status: str) -> dict:
        d = self._ensure()
        if status == "rejected":
            d["outlets"] = [o for o in d["outlets"] if o.get("outlet_id") != outlet_id]
        else:  # active: clear probation so the outlet is trusted in the next edition
            for o in d["outlets"]:
                if o.get("outlet_id") == outlet_id:
                    o["status"] = "active"
        _save(self.path, d)
        return d

    def discard(self) -> None:
        self._draft = None
        if os.path.exists(self.path):
            os.remove(self.path)


class FundingStore:
    """Display mirror of on-chain brief funding (HRLDFUND) — who is funding which brief and the α
    they hold. NOT authoritative: the validator reads the brief's signed `boost`, which the operator
    sets from these holdings. Keyed by funder hotkey (one funding commit per hotkey).
    """

    def __init__(self, path: str, max_items: int = _MAX_RESULTS):
        self.path = path
        self.max_items = max_items
        self._items = _load(path, {})

    def add(self, item: dict):
        key = item.get("funder")
        if not key:
            return
        self._items[key] = item
        while len(self._items) > self.max_items:
            self._items.pop(next(iter(self._items)))  # bound disk growth; evict oldest
        _save(self.path, self._items)

    def all(self) -> List[dict]:
        return list(self._items.values())


class DisputeStore:
    """Display mirror of on-chain disputes (HRLDDIS) for the console — NOT authoritative.

    Validators read disputes from chain; this only lets the UI list "placements under dispute" and
    who filed them. Keyed by article_id (one active dispute per article, mirroring the protocol).
    """

    def __init__(self, path: str, max_items: int = _MAX_RESULTS):
        self.path = path
        self.max_items = max_items
        self._items = _load(path, {})

    def add(self, item: dict):
        key = item.get("article_id")
        if not key:
            return
        self._items[key] = item
        while len(self._items) > self.max_items:
            self._items.pop(next(iter(self._items)))  # bound disk growth; evict oldest
        _save(self.path, self._items)

    def all(self) -> List[dict]:
        return list(self._items.values())


class RevealStore:
    """Commit reveals posted by miners (e.g. from the dashboard) for the neuron to serve.

    Keyed by the on-chain commitment value; each holds the salt (nonce) and committed fields.
    Token-gated on read and write — the nonce is a secret.
    """

    def __init__(self, path: str, max_items: int = _MAX_RESULTS):
        self.path = path
        self.max_items = max_items
        self._items = _load(path, {})

    def add(self, item: dict):
        key = item.get("onchain")
        if not key:
            return
        self._items[key] = item
        while len(self._items) > self.max_items:
            self._items.pop(next(iter(self._items)))  # bound disk growth; evict oldest
        _save(self.path, self._items)

    def all(self) -> List[dict]:
        return list(self._items.values())

    def get(self, onchain: str) -> Optional[dict]:
        return self._items.get(onchain)
