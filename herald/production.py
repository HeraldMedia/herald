"""Fail-closed configuration checks for production neurons."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


class ProductionConfigurationError(RuntimeError):
    pass


def _enabled(env, name: str) -> bool:
    return str(env.get(name, "")).lower() in ("1", "true", "yes")


def _local_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return host == "localhost" or host == "::1" or host.startswith("127.")


def _load_registry(env) -> tuple[dict | None, str | None]:
    path = env.get("HERALD_REGISTRY_PATH")
    if not path:
        return None, "HERALD_REGISTRY_PATH is required"
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError):
        return None, "HERALD_REGISTRY_PATH must point to a readable registry"


def validator_environment_errors(
    env=None, *, network: str = None, netuid: int = None,
    actual_consensus: str = None,
) -> list[str]:
    env = os.environ if env is None else env
    errors = []
    network = str(network or env.get("SUBTENSOR_NETWORK", ""))
    try:
        configured_netuid = int(netuid if netuid is not None else env.get("NETUID", "-1"))
        production_netuid = int(env.get("HERALD_PRODUCTION_NETUID", "69"))
    except (TypeError, ValueError):
        configured_netuid, production_netuid = -1, 69

    if network != "finney":
        errors.append("production validators must use the finney network")
    if configured_netuid != production_netuid:
        errors.append(f"production validators must use netuid {production_netuid}")
    if _enabled(env, "HERALD_ALLOW_LOCAL_FETCH"):
        errors.append("local fetch must be disabled in production")
    if env.get("HERALD_SIM_PROVIDER_BASE"):
        errors.append("simulator endpoints are forbidden in production")
    if _enabled(env, "HERALD_ALLOW_OPEN_WRITES"):
        errors.append("open writes must be disabled in production")
    for name in (
        "HERALD_NYT_API_BASE", "HERALD_SCRAPINGBEE_BASE",
        "HERALD_SERPAPI_BASE", "HERALD_BRAVE_BASE",
    ):
        if env.get(name) and _local_url(env[name]):
            errors.append(f"{name} cannot use a localhost endpoint in production")

    if not _enabled(env, "HERALD_REQUIRE_SIGNED_REGISTRY"):
        errors.append("signed registry enforcement is required")
    pubkey = env.get("HERALD_REGISTRY_PUBKEY")
    if not pubkey:
        errors.append("HERALD_REGISTRY_PUBKEY is required")
    if not env.get("HERALD_REGISTRY_AUTHORITY_HOTKEY"):
        errors.append("HERALD_REGISTRY_AUTHORITY_HOTKEY is required")
    registry, registry_error = _load_registry(env)
    if registry_error:
        errors.append(registry_error)
    elif pubkey:
        from herald.validator.news.registry_signing import verify
        try:
            valid_signature = verify(registry, pubkey)
        except (TypeError, ValueError):
            valid_signature = False
        if not valid_signature:
            errors.append("registry signature verification failed")

    if not _enabled(env, "HERALD_REQUIRE_SIGNED_BRIEFS"):
        errors.append("signed briefs must be required in production")
    if not env.get("HERALD_BRIEFS_PUBKEY"):
        errors.append("HERALD_BRIEFS_PUBKEY is required")
    for name in ("HERALD_BRIEFS_ENDPOINT", "HERALD_RESULTS_ENDPOINT"):
        value = env.get(name, "")
        if not value:
            errors.append(f"{name} is required")
        elif _local_url(value):
            errors.append(f"{name} cannot use localhost in production")
    if not env.get("HERALD_RESULTS_TOKEN"):
        errors.append("HERALD_RESULTS_TOKEN is required")

    strategies = [str(outlet.get("fetch", "direct")) for outlet in (registry or {}).get("outlets", [])]
    if any(strategy == "proxy" or strategy.startswith("proxy:") for strategy in strategies):
        if not env.get("SCRAPINGBEE_API_KEY"):
            errors.append("SCRAPINGBEE_API_KEY is required by the signed registry")
    if "api:nyt" in strategies and not env.get("HERALD_NYT_API_KEY"):
        errors.append("HERALD_NYT_API_KEY is required by the signed registry")
    if not (env.get("SERPAPI_API_KEY") or env.get("BRAVE_API_KEY")):
        errors.append("at least one search provider credential is required")

    expected = env.get("HERALD_EXPECTED_CONSENSUS_FP")
    if not expected:
        errors.append("HERALD_EXPECTED_CONSENSUS_FP is required")
    elif actual_consensus is not None and expected != actual_consensus:
        errors.append("validator consensus fingerprint does not match HERALD_EXPECTED_CONSENSUS_FP")
    return errors


def validate_neuron_environment(
    role: str, network: str, netuid: int, env=None, *, actual_consensus: str = None,
) -> None:
    env = os.environ if env is None else env
    if not _enabled(env, "HERALD_PRODUCTION"):
        return
    if role != "ValidatorNeuron":
        errors = []
        expected_netuid = int(env.get("HERALD_PRODUCTION_NETUID", "69"))
        if str(network) != "finney":
            errors.append("production neurons must use the finney network")
        if int(netuid) != expected_netuid:
            errors.append(f"production neurons must use netuid {expected_netuid}")
    else:
        if actual_consensus is None:
            from herald.validator.utils.consensus import consensus_fingerprint
            actual_consensus = consensus_fingerprint()
        errors = validator_environment_errors(
            env, network=network, netuid=netuid, actual_consensus=actual_consensus,
        )
    if errors:
        raise ProductionConfigurationError("production preflight failed: " + "; ".join(errors))


def validate_registry_anchor(
    subtensor, netuid: int, env=None, *, commitments_loader=None,
    registry_loader=None, current_block: int = None,
) -> None:
    env = os.environ if env is None else env
    if not _enabled(env, "HERALD_PRODUCTION"):
        return
    if commitments_loader is None:
        from herald.validator.news.chain import get_commitments_with_block
        commitments_loader = get_commitments_with_block
    if registry_loader is None:
        from herald.validator.news.registry import load_registry
        registry_loader = load_registry
    authority = env.get("HERALD_REGISTRY_AUTHORITY_HOTKEY", "")
    commitments = commitments_loader(subtensor, netuid)
    record = commitments.get(authority)
    if record is None:
        raise ProductionConfigurationError("registry authority has no on-chain commitment")
    anchor = record[0] if isinstance(record, tuple) else record
    if current_block is None:
        current_block = subtensor.get_current_block()
    try:
        registry_loader(
            anchor,
            require_anchor=True,
            current_block=current_block,
            network="finney",
            netuid=netuid,
        )
    except Exception as exc:
        raise ProductionConfigurationError(f"registry anchor verification failed: {exc}") from exc


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m herald.production")
    parser.add_argument("command", choices=("fingerprint", "check-validator"))
    args = parser.parse_args()
    if args.command == "fingerprint":
        from herald.validator.utils.consensus import consensus_fingerprint
        print(consensus_fingerprint())
        return
    validate_neuron_environment(
        "ValidatorNeuron",
        os.environ.get("SUBTENSOR_NETWORK", ""),
        int(os.environ.get("NETUID", "-1")),
    )
    print("production validator preflight passed")


if __name__ == "__main__":
    main()
