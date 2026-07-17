"""Local store of a miner's commitments and their reveal records."""

import json
import os
import secrets
from typing import List, Optional

from herald.commit import commit_hash, encode
from herald.evidence import clean_evidence, evidence_hash


class ClaimStore:
    def __init__(self, path: str):
        self.path = path
        self._records = {}
        self._reload()

    def _reload(self):
        """Reload records written by another process, such as the miner CLI."""
        if not os.path.exists(self.path):
            self._records = {}
            return
        with open(self.path, "r", encoding="utf-8") as f:
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

    def add(self, *, brief_id, target_outlet_id, claimer_hotkey, bond_atto, version_id,
            evidence: dict = None) -> str:
        nonce = secrets.token_hex(16)
        evidence = clean_evidence(evidence)
        pre_hash = evidence_hash(evidence) if evidence else ""
        onchain = encode(commit_hash(
            brief_id=brief_id, target_outlet_id=target_outlet_id,
            claimer_hotkey=claimer_hotkey, nonce=nonce,
            bond_atto=bond_atto, version_id=version_id, pre_hash=pre_hash,
        ))
        self._records[onchain] = {
            "brief_id": brief_id,
            "target_outlet_id": target_outlet_id,
            "claimer_hotkey": claimer_hotkey,
            "nonce": nonce,
            "bond_atto": bond_atto,
            "version_id": version_id,
            "pre_hash": pre_hash,
            "evidence": evidence,
            "article_url": None,
        }
        self._save()
        return onchain

    def import_record(self, reveal: dict) -> str:
        """Import an externally-built reveal (e.g. posted to the brief board from the dashboard),
        keeping its existing on-chain value + nonce so the served claim matches the commitment.
        Recomputes the on-chain value from the fields and rejects a tampered reveal."""
        fields = dict(
            brief_id=reveal["brief_id"],
            target_outlet_id=reveal["target_outlet_id"],
            claimer_hotkey=reveal["claimer_hotkey"],
            nonce=reveal["nonce"],
            bond_atto=int(reveal["bond_atto"]),
            version_id=int(reveal["version_id"]),
        )
        evidence = clean_evidence(reveal.get("evidence"))
        pre_hash = str(reveal.get("pre_hash") or "")
        if evidence and evidence_hash(evidence) != pre_hash:
            raise ValueError("reveal evidence does not match its pre_hash")
        onchain = reveal["onchain"]
        if onchain != encode(commit_hash(**fields, pre_hash=pre_hash)):
            raise ValueError("reveal onchain value does not match its fields")
        self._records[onchain] = {**fields, "pre_hash": pre_hash, "evidence": evidence,
                                  "article_url": reveal.get("article_url"),
                                  "snapshot_text": (reveal.get("snapshot_text") or None)}
        self._save()
        return onchain

    def set_article_url(self, onchain: str, url: str, snapshot_text: str = None):
        self._records[onchain]["article_url"] = url
        if snapshot_text:
            self._records[onchain]["snapshot_text"] = snapshot_text[:30_000]
        self._save()

    def get(self, onchain: str) -> Optional[dict]:
        return self._records.get(onchain)

    def active_claims(self) -> List[dict]:
        # Commit/attach commands run in a separate CLI process. Reload here so a
        # running miner serves newly attached (or removed) claims immediately.
        self._reload()
        return [r for r in self._records.values() if r.get("article_url")]
