#!/usr/bin/env python3
"""Read-only liveness watchdog for a Herald validator.

It runs two checks and writes nothing anywhere:

  last_update     Age in blocks of the validator hotkey's on-chain LastUpdate on the subnet. The
                  default threshold is the subnet's ActivityCutoff: past it the chain stops counting
                  that validator's weights.
  snapshot_epoch  The newest epoch the backend reports (GET /public/epochs), compared with the
                  current Herald epoch computed from the chain head exactly as forward.py does.

Exit status: 0 healthy, 1 breach, 2 a check could not run (chain or backend unreachable, or an
unexpected payload). A breach outranks a check that could not run.

It deliberately ignores the weight-epoch checkpoint inside the validator's state file. That value only
records this validator's own accepted submissions, so it can neither prove nor disprove liveness.

Example, from the root of a host checkout with the requirements installed (the validator image does
not include scripts/):
  python scripts/watchdog.py --hotkey <validator ss58> --netuid 69 --network finney --backend-url https://api.heraldmedia.ai
"""

import argparse
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

EXIT_OK = 0
EXIT_BREACH = 1
EXIT_UNKNOWN = 2

OK = "ok"
BREACH = "breach"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Finding:
    check: str
    status: str
    message: str


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def herald_epoch(block: int, epoch_len: int, epoch_lag: int) -> int:
    """The validator's evaluation epoch for a chain block (mirrors forward.py)."""
    return max(0, int(block) - int(epoch_lag)) // int(epoch_len)


def check_last_update(*, hotkey: str, netuid: int, uid: Optional[int], current_block: int,
                      last_update_block: Optional[int], max_age_blocks: int) -> Finding:
    if uid is None:
        return Finding("last_update", BREACH,
                       f"hotkey {hotkey} is not registered on netuid {netuid}")
    if last_update_block is None:
        return Finding("last_update", UNKNOWN,
                       f"chain returned no LastUpdate entry for uid {uid} on netuid {netuid}")
    age = max(0, int(current_block) - int(last_update_block))
    detail = (f"validator {hotkey} (uid {uid}) last updated weights at block {last_update_block}, "
              f"{age} blocks before head {current_block}; threshold {max_age_blocks} blocks")
    if age > int(max_age_blocks):
        return Finding("last_update", BREACH, f"weights are stale: {detail}")
    return Finding("last_update", OK, detail)


def latest_snapshot(decisions) -> Optional[tuple]:
    """(epoch, decision) for the newest epoch in a /public/epochs payload, or None."""
    best = None
    for decision in decisions:
        if not isinstance(decision, dict) or isinstance(decision.get("epoch"), bool):
            continue
        try:
            epoch = int(decision.get("epoch"))
        except (TypeError, ValueError):
            continue
        if best is None or epoch > best[0]:
            best = (epoch, decision)
    return best


def check_snapshot_epoch(*, network: str, netuid: int, current_block: int, decisions: list,
                         max_epoch_lag: int, epoch_len: int, epoch_lag: int) -> Finding:
    chain_epoch = herald_epoch(current_block, epoch_len, epoch_lag)
    latest = latest_snapshot(decisions)
    if latest is None:
        if decisions:
            return Finding("snapshot_epoch", UNKNOWN,
                           f"backend returned {len(decisions)} epoch row(s) without a readable epoch")
        return Finding("snapshot_epoch", BREACH,
                       f"backend reports no validator snapshot epochs for {network} netuid {netuid}; "
                       f"chain epoch is {chain_epoch}")
    epoch, decision = latest
    snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
    reporter = snapshot.get("validator_hotkey") or "unknown reporter"
    lag = chain_epoch - epoch
    detail = (f"latest reported snapshot epoch {epoch} (status {decision.get('status', 'unknown')}, "
              f"reporter {reporter}); chain epoch {chain_epoch} at block {current_block}; "
              f"lag {lag}, threshold {max_epoch_lag}")
    if lag > int(max_epoch_lag):
        return Finding("snapshot_epoch", BREACH, f"snapshots are stale: {detail}")
    if lag < 0:
        return Finding("snapshot_epoch", BREACH,
                       f"backend epoch is ahead of the chain (epoch length or lag mismatch?): {detail}")
    return Finding("snapshot_epoch", OK, detail)


class ChainReader:
    """Read-only chain access through bittensor, imported lazily so tests never need it."""

    def __init__(self, network: str):
        import bittensor as bt

        self._subtensor = bt.Subtensor(network=network)

    def current_block(self) -> int:
        return int(self._subtensor.get_current_block())

    def uid_for_hotkey(self, hotkey: str, netuid: int) -> Optional[int]:
        uid = self._subtensor.get_uid_for_hotkey_on_subnet(hotkey, netuid)
        return None if uid is None else int(uid)

    def last_update(self, netuid: int, uid: int) -> Optional[int]:
        values = self._subtensor.get_hyperparameter("LastUpdate", netuid=netuid)
        if not values or uid >= len(values):
            return None
        return int(values[uid])

    def activity_cutoff(self, netuid: int) -> int:
        return int(self._subtensor.get_hyperparameter("ActivityCutoff", netuid=netuid))


class BackendReader:
    """GET-only access to the Herald backend's public API."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def epochs(self, network: str, netuid: int) -> list:
        import httpx

        response = httpx.get(f"{self.base_url}/public/epochs",
                             params={"network": network, "netuid": netuid}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"expected a JSON list from /public/epochs, got {type(payload).__name__}")
        return payload


def run_checks(chain, backend, *, hotkey: str, netuid: int, network: str,
               max_update_age_blocks: Optional[int], max_epoch_lag: int,
               epoch_len: int, epoch_lag: int) -> List[Finding]:
    try:
        block = int(chain.current_block())
    except Exception as e:
        return [Finding("chain", UNKNOWN, f"cannot read the chain head: {_describe(e)}")]

    findings = []
    try:
        uid = chain.uid_for_hotkey(hotkey, netuid)
        if uid is None:
            findings.append(check_last_update(hotkey=hotkey, netuid=netuid, uid=None,
                                              current_block=block, last_update_block=None,
                                              max_age_blocks=0))
        else:
            threshold = (int(max_update_age_blocks) if max_update_age_blocks is not None
                         else int(chain.activity_cutoff(netuid)))
            findings.append(check_last_update(hotkey=hotkey, netuid=netuid, uid=uid,
                                              current_block=block,
                                              last_update_block=chain.last_update(netuid, uid),
                                              max_age_blocks=threshold))
    except Exception as e:
        findings.append(Finding("last_update", UNKNOWN,
                                f"cannot read LastUpdate for {hotkey} on netuid {netuid}: {_describe(e)}"))

    try:
        decisions = backend.epochs(network, netuid)
        findings.append(check_snapshot_epoch(network=network, netuid=netuid, current_block=block,
                                             decisions=decisions, max_epoch_lag=max_epoch_lag,
                                             epoch_len=epoch_len, epoch_lag=epoch_lag))
    except Exception as e:
        findings.append(Finding("snapshot_epoch", UNKNOWN,
                                f"cannot read snapshot epochs from the backend: {_describe(e)}"))
    return findings


def exit_code(findings: List[Finding]) -> int:
    statuses = {finding.status for finding in findings}
    if BREACH in statuses:
        return EXIT_BREACH
    if UNKNOWN in statuses or not findings:
        return EXIT_UNKNOWN
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Herald validator liveness watchdog")
    parser.add_argument("--hotkey", required=True,
                        help="validator hotkey (ss58) whose on-chain LastUpdate is checked")
    parser.add_argument("--netuid", type=int, default=int(os.getenv("NETUID", "69")))
    parser.add_argument("--network", default=os.getenv("SUBTENSOR_NETWORK") or "finney",
                        help="network label for the backend query; also the chain unless "
                             "--chain-endpoint is given")
    parser.add_argument("--chain-endpoint", default=os.getenv("SUBTENSOR_CHAIN_ENDPOINT") or None,
                        help="websocket endpoint to read the chain from (default: --network)")
    parser.add_argument("--backend-url",
                        default=os.getenv("HERALD_RESULTS_ENDPOINT") or "https://api.heraldmedia.ai")
    parser.add_argument("--max-update-age-blocks", type=int, default=None,
                        help="breach when LastUpdate is older (default: on-chain ActivityCutoff)")
    parser.add_argument("--max-epoch-lag", type=int, default=1,
                        help="breach when the latest snapshot epoch trails the chain epoch by more")
    parser.add_argument("--epoch-len", type=int, default=None,
                        help="blocks per Herald epoch (default: $HERALD_VEST_EPOCH_LEN or 7200)")
    parser.add_argument("--epoch-lag", type=int, default=None,
                        help="epoch boundary lag in blocks (default: $HERALD_EPOCH_LAG or 10)")
    return parser


def _herald_epoch_defaults() -> tuple:
    """(HERALD_VEST_EPOCH_LEN, HERALD_EPOCH_LAG), read exactly as herald/validator/utils/config.py does.

    Taken from the environment instead of importing herald: importing the package initialises the
    validator's on-disk caches, and this watchdog must not write anything. tests/news/test_watchdog.py
    pins these defaults to config.py so the two cannot drift apart.
    """
    return int(os.getenv("HERALD_VEST_EPOCH_LEN", "7200")), int(os.getenv("HERALD_EPOCH_LAG", "10"))


def _collect(args, chain_factory, backend_factory) -> List[Finding]:
    default_len, default_lag = _herald_epoch_defaults()
    epoch_len = default_len if args.epoch_len is None else args.epoch_len
    epoch_lag = default_lag if args.epoch_lag is None else args.epoch_lag
    chain_target = args.chain_endpoint or args.network
    try:
        chain = chain_factory(chain_target)
    except Exception as e:
        return [Finding("chain", UNKNOWN, f"cannot connect to {chain_target}: {_describe(e)}")]
    return run_checks(
        chain, backend_factory(args.backend_url),
        hotkey=args.hotkey, netuid=args.netuid, network=args.network,
        max_update_age_blocks=args.max_update_age_blocks, max_epoch_lag=args.max_epoch_lag,
        epoch_len=epoch_len, epoch_lag=epoch_lag,
    )


def main(argv=None, *, chain_factory=None, backend_factory=None, out=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        findings = _collect(args, chain_factory or ChainReader, backend_factory or BackendReader)
    except Exception as e:  # a crashing watchdog must read as "could not run" (2), never breach (1)
        findings = [Finding("watchdog", UNKNOWN, f"could not run: {_describe(e)}")]

    for finding in findings:
        print(f"{finding.status.upper():8} {finding.check}: {finding.message}", file=out)
    code = exit_code(findings)
    summary = {EXIT_OK: "healthy", EXIT_BREACH: "BREACH",
               EXIT_UNKNOWN: "INCOMPLETE (a check could not run)"}[code]
    print(f"watchdog: {summary}", file=out)
    return code


if __name__ == "__main__":
    sys.exit(main())
