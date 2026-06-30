"""Local store of a miner's commitments and their reveal records."""

import json
import os
import secrets
from typing import List, Optional

from herald.commit import commit_hash, encode


class ClaimStore:
    def __init__(self, path: str):
        self.path = path
        self._records = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._records = json.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # 0600 + atomic rename: the nonces are the commit salts; a crash must not truncate
        # the file (losing unrevealable commitments) and other users must not read them.
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._records, f, indent=2)
        os.replace(tmp, self.path)

    def add(self, *, brief_id, target_outlet_id, claimer_hotkey, bond_atto, version_id) -> str:
        nonce = secrets.token_hex(16)
        onchain = encode(commit_hash(
            brief_id=brief_id, target_outlet_id=target_outlet_id,
            claimer_hotkey=claimer_hotkey, nonce=nonce,
            bond_atto=bond_atto, version_id=version_id,
        ))
        self._records[onchain] = {
            "brief_id": brief_id,
            "target_outlet_id": target_outlet_id,
            "claimer_hotkey": claimer_hotkey,
            "nonce": nonce,
            "bond_atto": bond_atto,
            "version_id": version_id,
            "article_url": None,
        }
        self._save()
        return onchain

    def set_article_url(self, onchain: str, url: str):
        self._records[onchain]["article_url"] = url
        self._save()

    def get(self, onchain: str) -> Optional[dict]:
        return self._records.get(onchain)

    def active_claims(self) -> List[dict]:
        return [r for r in self._records.values() if r.get("article_url")]
