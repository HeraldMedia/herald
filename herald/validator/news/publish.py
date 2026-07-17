"""Publish legacy result rows and hotkey-signed deterministic epoch snapshots."""

import hashlib
import json
import os

import bittensor as bt
import httpx


SNAPSHOT_DOMAIN = b"HERALD_VALIDATOR_SNAPSHOT_V1\n"
RECEIPT_DOMAIN = b"HERALD_WEIGHT_RECEIPT_V1\n"


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _microusd(value) -> int:
    return int(round(max(0.0, float(value or 0)) * 1_000_000))


def normalized_u16(weights, uids: list, hotkey_by_uid: dict) -> list:
    """Largest-remainder encoding that sums to exactly uint16 max."""
    positive = [(int(uid), max(0.0, float(weight))) for uid, weight in zip(uids, weights)
                if float(weight) > 0 and uid in hotkey_by_uid]
    if not positive:
        return []
    total = sum(weight for _uid, weight in positive)
    raw = [(uid, weight / total * 65535) for uid, weight in positive]
    floors = {uid: int(value) for uid, value in raw}
    remainder = 65535 - sum(floors.values())
    order = sorted(raw, key=lambda row: (-(row[1] - int(row[1])), row[0]))
    for uid, _value in order[:remainder]:
        floors[uid] += 1
    return [{"uid": uid, "hotkey": hotkey_by_uid[uid], "weight_u16": floors[uid]}
            for uid in sorted(floors)]


def vector_hash(weights: list) -> str:
    return hashlib.sha256(canonical_bytes(weights)).hexdigest()


def build_result_items(vesting, *, network: str, netuid: int, validator_hotkey: str,
                       validator_uid: int, chain_block: int, registry_version: int,
                       consensus: str) -> list:
    """Build a network-scoped public projection of every persisted placement.

    Publishing the ledger rather than only this cycle's freshly pulled winners means the public
    API continues to receive completion, expiry and clawback status after a miner stops serving the
    original claim. The backend merges these lifecycle updates with the first full reveal.
    """
    items = []
    entries = vesting.to_dict().get("entries", {})
    for article_id, entry in sorted(entries.items()):
        total_usd = float(entry["total_usd"])
        remaining = int(entry["remaining"])
        installment_usd = float(entry["installment_usd"])
        item = {
            "article_id": article_id,
            "hotkey": entry.get("hotkey", ""),
            "brief_id": entry.get("brief_id", ""),
            "url": entry.get("url", ""),
            "usd": total_usd,
            "earned_usd": max(0.0, total_usd - installment_usd * remaining),
            "installment_usd": installment_usd,
            "remaining": remaining,
            "status": entry["status"],
            "commit_epoch": int(entry.get("commit_epoch", 0)),
            "start_epoch": int(entry.get("start_epoch", 0)),
            "network": str(network),
            "netuid": int(netuid),
            "validator_hotkey": validator_hotkey,
            "validator_uid": int(validator_uid),
            "chain_block": int(chain_block),
            "registry_version": int(registry_version),
            "consensus": consensus,
        }
        if entry.get("outlet_id"):
            item["outlet_id"] = entry["outlet_id"]
        if int(entry.get("tier", 0)) in (1, 2, 3):
            item["tier"] = int(entry["tier"])
        item["attribution"] = int(entry.get("attribution", 0))
        if entry.get("reveal"):
            item["reveal"] = dict(entry["reveal"])
        items.append(item)
    return items


def publish_results(endpoint: str, items: list):
    if not endpoint or not items:
        return
    token = os.getenv("HERALD_RESULTS_TOKEN")
    headers = {"X-Results-Token": token} if token else {}
    for item in items:
        try:
            response = httpx.post(f"{endpoint}/results", json=item, timeout=5.0, headers=headers)
            response.raise_for_status()
        except Exception as e:
            bt.logging.warning(f"Result publish failed: {e}")


def build_epoch_snapshot(vesting, briefs: list, pool_spent: dict, rewards_by_uid: dict,
                         weights, uids: list, hotkey_by_uid: dict, *, network: str,
                         netuid: int, validator_hotkey: str, validator_uid: int,
                         chain_block: int, epoch: int, registry_version: int,
                         registry_hash: str, consensus: str) -> dict:
    articles = []
    for item in build_result_items(
        vesting, network=network, netuid=netuid, validator_hotkey=validator_hotkey,
        validator_uid=validator_uid, chain_block=chain_block,
        registry_version=registry_version, consensus=consensus,
    ):
        total = float(item.pop("usd", 0) or 0)
        installment = float(item.pop("installment_usd", 0) or 0)
        earned = float(item.pop("earned_usd", 0) or 0)
        # Reporter identity and observation block belong to the signed envelope. Keeping them in
        # consensus state would make two honest validators hash the same article differently.
        for key in ("network", "netuid", "validator_hotkey", "validator_uid", "chain_block",
                    "registry_version", "consensus"):
            item.pop(key, None)
        articles.append({
            **item, "total_microusd": _microusd(total),
            "installment_microusd": _microusd(installment),
            "earned_microusd": _microusd(earned),
        })
    article_rows = sorted(articles, key=lambda row: row["article_id"])
    brief_rows = []
    for brief in sorted(briefs, key=lambda row: str(row.get("id", ""))):
        brief_id = str(brief.get("id", ""))
        standing = brief.get("kind") == "standing"
        total = None if standing else _microusd(brief.get("reward_pool", 0))
        spent = 0 if standing else _microusd(pool_spent.get(brief_id, 0))
        brief_rows.append({
            "brief_id": brief_id, "kind": brief.get("kind", "client"),
            "pool_total_microusd": total, "pool_spent_microusd": spent,
            "pool_remaining_microusd": None if total is None else max(0, total - spent),
        })
    reward_rows = [{"uid": int(uid), "hotkey": hotkey_by_uid[uid],
                    "reward_microusd": _microusd(value)}
                   for uid, value in sorted(rewards_by_uid.items()) if uid in hotkey_by_uid]
    weight_rows = normalized_u16(weights, uids, hotkey_by_uid)
    item = {
        "schema_version": 1, "network": str(network), "netuid": int(netuid),
        "epoch": int(epoch), "chain_block": int(chain_block),
        "validator_hotkey": validator_hotkey, "validator_uid": int(validator_uid),
        "consensus": consensus, "registry_version": int(registry_version),
        "registry_hash": registry_hash,
        "state": {"articles": article_rows, "briefs": brief_rows,
                  "rewards": reward_rows, "weights": weight_rows},
    }
    material = {key: item[key] for key in (
        "schema_version", "network", "netuid", "epoch", "consensus",
        "registry_version", "registry_hash", "state",
    )}
    item["state_hash"] = hashlib.sha256(canonical_bytes(material)).hexdigest()
    return item


def sign_snapshot(item: dict, hotkey) -> dict:
    unsigned = {k: v for k, v in item.items() if k != "signature"}
    signature = hotkey.sign(SNAPSHOT_DOMAIN + canonical_bytes(unsigned))
    return {**unsigned, "signature": "0x" + bytes(signature).hex()}


def publish_snapshot(endpoint: str, item: dict, hotkey) -> bool:
    if not endpoint:
        return False
    token = os.getenv("HERALD_RESULTS_TOKEN")
    headers = {"X-Results-Token": token} if token else {}
    try:
        response = httpx.post(f"{endpoint.rstrip('/')}/api/v3/validator/snapshots",
                              json=sign_snapshot(item, hotkey), headers=headers, timeout=10.0)
        response.raise_for_status()
        return True
    except Exception as exc:
        bt.logging.warning(f"Snapshot publish failed: {exc}")
        return False


def publish_weight_receipt(endpoint: str, item: dict, hotkey) -> bool:
    if not endpoint:
        return False
    unsigned = {k: v for k, v in item.items() if k != "signature"}
    signature = hotkey.sign(RECEIPT_DOMAIN + canonical_bytes(unsigned))
    payload = {**unsigned, "signature": "0x" + bytes(signature).hex()}
    token = os.getenv("HERALD_RESULTS_TOKEN")
    headers = {"X-Results-Token": token} if token else {}
    try:
        response = httpx.post(f"{endpoint.rstrip('/')}/api/v3/validator/weight-receipts",
                              json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
        return True
    except Exception as exc:
        bt.logging.warning(f"Weight receipt publish failed: {exc}")
        return False
