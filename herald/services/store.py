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
