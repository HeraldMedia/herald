"""Verify that a confirmed backend epoch contains the expected end-to-end state."""

import argparse
import json
import time

import httpx


def _microusd(item: dict, micro_key: str, usd_key: str) -> int:
    if item.get(micro_key) is not None:
        return int(item[micro_key])
    return int(round(float(item.get(usd_key, 0) or 0) * 1_000_000))


def validate_payloads(decision: dict, articles: list, leaderboard: list,
                      economics: dict, requirements: dict) -> list[str]:
    errors = []
    target_epoch = int(requirements.get("target_epoch", 0))
    epoch = int(decision.get("epoch", -1))
    status = decision.get("status", "missing")
    attestations = int(decision.get("attestation_count", 0))
    required = int(decision.get("required_attestations", 2))
    if status != "confirmed":
        errors.append(f"epoch status is {status}, not confirmed")
    if epoch < target_epoch:
        errors.append(f"confirmed epoch {epoch} is older than target {target_epoch}")
    if attestations < required:
        errors.append(f"attestations {attestations}/{required} do not satisfy quorum")

    by_article = {str(row.get("article_id")): row for row in articles}
    for expected in requirements.get("articles", []):
        article_id = str(expected["article_id"])
        row = by_article.get(article_id)
        if row is None or row.get("confirmation_status") != "confirmed":
            errors.append(f"missing confirmed article {article_id}")
            continue
        for field in ("hotkey", "brief_id", "outlet_id", "url"):
            if field not in expected:
                continue
            if str(row.get(field, "")) != str(expected.get(field, "")):
                errors.append(
                    f"article {article_id} {field}={row.get(field)!r}, expected {expected.get(field)!r}"
                )
        earned = _microusd(row, "earned_microusd", "earned_usd")
        minimum = int(expected.get("min_earned_microusd", 0))
        if earned < minimum:
            errors.append(f"article {article_id} earned {earned} microusd, expected at least {minimum}")

    by_miner = {str(row.get("hotkey")): row for row in leaderboard}
    for hotkey in requirements.get("miners", []):
        row = by_miner.get(str(hotkey))
        if row is None:
            errors.append(f"missing rewarded miner {hotkey}")
            continue
        if int(row.get("articles", 0)) < 1 or float(row.get("total_usd", 0) or 0) <= 0:
            errors.append(f"miner {hotkey} has no released article value")
        if float(row.get("daily_reward_usd", 0) or 0) <= 0:
            errors.append(f"miner {hotkey} has no daily reward")
        if float(row.get("intended_weight", 0) or 0) <= 0:
            errors.append(f"miner {hotkey} has no intended weight")

    for expected in requirements.get("briefs", []):
        brief_id = str(expected["brief_id"])
        row = economics.get(brief_id)
        if row is None or row.get("confirmation_status") != "confirmed":
            errors.append(f"missing confirmed economics for brief {brief_id}")
            continue
        if int(row.get("epoch", -1)) < target_epoch:
            errors.append(f"brief {brief_id} economics are older than target epoch {target_epoch}")
        spent = _microusd(row, "pool_spent_microusd", "pool_spent_usd")
        minimum = int(expected.get("min_pool_spent_microusd", 0))
        if spent < minimum:
            errors.append(f"brief {brief_id} spent {spent} microusd, expected at least {minimum}")
    return errors


def _get(client: httpx.Client, path: str, scope: dict):
    response = client.get(path, params=scope)
    response.raise_for_status()
    return response.json()


def wait_for_confirmation(endpoint: str, network: str, netuid: int, requirements: dict,
                          timeout: int, interval: int) -> dict:
    deadline = time.monotonic() + timeout
    scope = {"network": network, "netuid": netuid}
    last_errors = ["no epoch decision published"]
    with httpx.Client(base_url=endpoint.rstrip("/"), timeout=15.0) as client:
        while time.monotonic() < deadline:
            try:
                decisions = _get(client, "/public/epochs", scope)
                decision = next((row for row in decisions if row.get("status") == "confirmed"),
                                decisions[0] if decisions else {})
                articles = _get(client, "/public/articles", scope)
                leaderboard = _get(client, "/public/leaderboard", scope)
                economics = {}
                for brief in requirements.get("briefs", []):
                    brief_id = str(brief["brief_id"])
                    response = client.get(f"/public/briefs/{brief_id}/economics", params=scope)
                    economics[brief_id] = response.json() if response.is_success else None
                last_errors = validate_payloads(
                    decision, articles if isinstance(articles, list) else [],
                    leaderboard if isinstance(leaderboard, list) else [], economics, requirements,
                )
                print(
                    f"epoch={decision.get('epoch', -1)} status={decision.get('status', 'none')} "
                    f"attestations={decision.get('attestation_count', 0)}/"
                    f"{decision.get('required_attestations', 2)} checks={len(last_errors)}"
                )
                if not last_errors:
                    return decision
            except (httpx.HTTPError, TypeError, ValueError) as exc:
                last_errors = [str(exc)]
            time.sleep(interval)
    raise RuntimeError("; ".join(last_errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--netuid", required=True, type=int)
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    with open(args.requirements, encoding="utf-8") as handle:
        requirements = json.load(handle)
    try:
        decision = wait_for_confirmation(
            args.endpoint, args.network, args.netuid, requirements, args.timeout, args.interval,
        )
    except RuntimeError as exc:
        print(f"Timed out waiting for complete confirmed quorum: {exc}")
        return 1
    print(json.dumps({key: decision.get(key) for key in (
        "network", "netuid", "epoch", "status", "state_hash", "consensus",
        "registry_hash", "attestation_count", "required_attestations",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
