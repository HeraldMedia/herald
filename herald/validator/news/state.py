"""Persistent Herald validator state: commit index, vesting, and slash ledgers.

This file is the only record of in-flight placements, so it must survive a rollback, damage and
operator error:

* ``schema_version`` is written at the top level. A file without it is legacy schema 1 and loads as
  before. Schema 2 adds only that key, which the pre-schema loader ignores, so a validator rolled
  back to that code still loads what this code wrote (see tests/news/test_state_rollback.py).
* A file from a newer writer loads what this version understands, after the original is copied
  aside as ``<path>.schema<N>.<UTC timestamp>``, because this version's next save rewrites it as
  schema 2. A newer writer whose change an older reader would misread also writes
  ``min_reader_schema``; a reader below it refuses to start unless
  ``HERALD_STATE_ALLOW_NEWER_SCHEMA=true``.
* ``load`` refuses to start when the file exists but cannot be parsed or validated (structure and
  value types), or is missing while backups of it exist: once money is owed, an empty ledger is
  never a safe default. A missing file with no backups is a first boot and starts fresh.
  ``HERALD_STATE_ALLOW_FRESH_ON_CORRUPT=true`` is the deliberate escape hatch: it copies an
  unreadable file aside (it is never deleted), renames a missing file's backups out of rotation,
  logs at ERROR and starts fresh. While either override is set, every load logs a WARNING.
* ``save`` stays atomic and keeps the file it replaces as ``<path>.bak.<UTC timestamp>``, pruned to
  the newest ``HERALD_STATE_BACKUP_KEEP`` (default 14; 0 disables backups and deletes none).
"""

import filecmp
import json
import os
import re
import shutil
from datetime import datetime, timezone

import bittensor as bt

from herald.validator.utils.config import EPOCH_LEN, VEST_EPOCHS
from .commit_index import CommitIndex
from .disputes import DisputeLedger
from .slashing import SlashLedger
from .vesting import VestingLedger

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1  # a file with no schema_version key predates the key

ALLOW_FRESH_ON_CORRUPT_ENV = "HERALD_STATE_ALLOW_FRESH_ON_CORRUPT"
ALLOW_NEWER_SCHEMA_ENV = "HERALD_STATE_ALLOW_NEWER_SCHEMA"
BACKUP_KEEP_ENV = "HERALD_STATE_BACKUP_KEEP"
DEFAULT_BACKUP_KEEP = 14

# min_reader_schema is read, never written: every reader down to schema 1 can load this version's saves.
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "min_reader_schema", "commit_index", "vesting", "slash", "disputes",
    "pool_spent", "last_scored_epoch", "last_weight_epoch",
})
_BACKUP_SUFFIX = re.compile(r"\.bak\.(\d{8}T\d{12}Z)(?:-(\d+))?")


class HeraldStateLoadError(RuntimeError):
    """The validator must not start: its state file cannot be parsed or validated, is missing while
    backups of it exist, or needs a newer reader."""

    def __init__(self, path: str, error: BaseException, detail: str = "", message: str = None):
        self.path = path
        self.error = error
        super().__init__(message or (
            f"Refusing to start: Herald state file {path} exists but cannot be loaded "
            f"({type(error).__name__}: {error}).{detail} It may hold in-flight placements, so the "
            f"validator will not continue with an empty ledger. Restore the newest backup "
            f"({path}.bak.<UTC timestamp>) or repair the file, then restart the container. To start "
            f"fresh deliberately, set {ALLOW_FRESH_ON_CORRUPT_ENV}=true and recreate the container; "
            f"the unreadable file is copied aside first and never deleted."
        ))


def _json_np_safe(o):
    """json.dump default: unbox numpy scalars (they carry a .item()) to native Python."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _backup_keep() -> int:
    raw = os.getenv(BACKUP_KEEP_ENV, "").strip()
    if not raw:
        return DEFAULT_BACKUP_KEEP
    try:
        return max(0, int(raw))
    except ValueError:
        bt.logging.warning(
            f"{BACKUP_KEEP_ENV}={raw!r} is not an integer; keeping {DEFAULT_BACKUP_KEEP} backups"
        )
        return DEFAULT_BACKUP_KEEP


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sibling(path: str, kind: str) -> str:
    """A new, unused ``<path>.<kind>.<UTC timestamp>`` name beside ``path``."""
    base = f"{path}.{kind}.{_utc_stamp()}"
    candidate, n = base, 0
    while os.path.lexists(candidate):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def schema_version_of(data: dict) -> int:
    version = data.get("schema_version", LEGACY_SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version < LEGACY_SCHEMA_VERSION:
        raise ValueError(f"invalid schema_version {version!r}")
    return version


def min_reader_schema_of(data: dict) -> int:
    """The oldest reader schema allowed to load this file. A writer raises it only for a change an
    older reader would misread, so files that merely add fields keep loading after a rollback."""
    version = schema_version_of(data)
    value = data.get("min_reader_schema", LEGACY_SCHEMA_VERSION)
    if (isinstance(value, bool) or not isinstance(value, int) or value < LEGACY_SCHEMA_VERSION
            or value > version):
        raise ValueError(f"invalid min_reader_schema {value!r} for schema_version {version}")
    return value


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mapping(data: dict, key: str, *, where: str = "", required: bool = False) -> dict:
    if key not in data:
        if required:
            raise KeyError(f"{where}{key}")
        return {}
    value = data[key]
    if not isinstance(value, dict):
        raise ValueError(f"{where}{key} must be a JSON object, got {type(value).__name__}")
    return value


def _check_values(mapping: dict, label: str, check, kind: str) -> dict:
    for key, value in mapping.items():
        if not check(value):
            raise ValueError(f"{label} {key!r} must be {kind}, got {type(value).__name__}")
    return mapping


def backup_paths(path: str) -> list:
    """Rotating backups of ``path`` written by ``HeraldState.save``, oldest first."""
    directory = os.path.dirname(path) or "."
    prefix = os.path.basename(path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in names:
        match = _BACKUP_SUFFIX.fullmatch(name[len(prefix):]) if name.startswith(prefix) else None
        if match:
            found.append(((match.group(1), int(match.group(2) or 0)), os.path.join(directory, name)))
    return [p for _key, p in sorted(found)]


def _backup_previous(path: str) -> None:
    """Copy the file about to be replaced to ``<path>.bak.<UTC timestamp>`` (best effort)."""
    target = _sibling(path, "bak")
    try:
        shutil.copy2(path, target)
    except OSError as e:
        try:
            os.remove(target)  # never leave a truncated file that looks like a good backup
        except OSError:
            pass
        bt.logging.error(
            f"Could not back up Herald state {path} before saving ({e}); saving without a backup"
        )


def _prune_backups(path: str, keep: int) -> None:
    backups = backup_paths(path)
    for old in backups[:max(0, len(backups) - keep)]:
        try:
            os.remove(old)
        except OSError as e:
            bt.logging.warning(f"Could not remove old Herald state backup {old}: {e}")


def _identical_copy(path: str, kind: str):
    """An existing ``<path>.<kind>.*`` sibling with the same bytes as ``path``, or None."""
    directory = os.path.dirname(path) or "."
    prefix = f"{os.path.basename(path)}.{kind}."
    try:
        names = sorted(name for name in os.listdir(directory) if name.startswith(prefix))
    except OSError:
        return None
    for name in names:
        candidate = os.path.join(directory, name)
        try:
            if filecmp.cmp(path, candidate, shallow=False):
                return candidate
        except OSError:
            continue
    return None


def _newer_schema(data):
    """``(schema_version, min_reader_schema)`` when a newer writer produced ``data``, else None."""
    if not isinstance(data, dict):
        raise ValueError(f"top level must be a JSON object, got {type(data).__name__}")
    version, min_reader = schema_version_of(data), min_reader_schema_of(data)
    return (version, min_reader) if version > SCHEMA_VERSION else None


def _admit_newer_schema(path: str, version: int, min_reader: int) -> None:
    """Load a newer writer's file only without silent loss.

    A file this reader would misread (``min_reader_schema`` above SCHEMA_VERSION) needs the explicit
    override. Any newer file is copied aside first, because this version's next save rewrites it as
    SCHEMA_VERSION and drops what it does not understand.
    """
    if min_reader > SCHEMA_VERSION and not _env_true(ALLOW_NEWER_SCHEMA_ENV):
        raise HeraldStateLoadError(path, ValueError(f"min_reader_schema {min_reader}"), message=(
            f"Refusing to start: Herald state file {path} has schema_version {version} and needs a "
            f"validator that reads schema {min_reader} or later; this one reads {SCHEMA_VERSION}. "
            f"Run the newer validator. To load it with this version anyway, dropping what this "
            f"version does not understand, set {ALLOW_NEWER_SCHEMA_ENV}=true and recreate the "
            f"container; the file is copied aside first and never deleted."
        ))
    existing = _identical_copy(path, f"schema{version}")
    if existing:
        # A restart before this version's first save: the copy from the previous start still holds.
        bt.logging.warning(
            f"Herald state {path} (schema_version {version}) is already preserved as {existing}; "
            f"this validator's next save rewrites it as schema_version {SCHEMA_VERSION}"
        )
        return
    preserved = _sibling(path, f"schema{version}")
    try:
        shutil.copy2(path, preserved)
    except OSError as e:
        raise HeraldStateLoadError(path, e, message=(
            f"Refusing to start: Herald state file {path} has schema_version {version}, newer than "
            f"this validator's {SCHEMA_VERSION}, and copying it aside to {preserved} failed ({e}). "
            f"This version's next save would drop what it does not understand, so it will not "
            f"load the file without that copy."
        )) from e
    bt.logging.warning(
        f"Preserved Herald state {path} (schema_version {version}) as {preserved} before this "
        f"validator's next save rewrites it as schema_version {SCHEMA_VERSION}"
    )


class HeraldState:
    def __init__(self, commit_index: CommitIndex, vesting: VestingLedger, slash: SlashLedger,
                 disputes: DisputeLedger = None, pool_spent: dict = None,
                 last_scored_epoch: int = -1, last_weight_epoch: int = -1):
        self.commit_index = commit_index
        self.vesting = vesting
        self.slash = slash
        self.disputes = disputes if disputes is not None else DisputeLedger()
        # {brief_id: cumulative USD drawn from that client brief's reward pool}, so a pool is never
        # over-paid across epochs. Standing briefs pay from emissions and never appear here.
        self.pool_spent = dict(pool_spent or {})
        # Persisted so a restart inside an already-scored epoch doesn't re-score it: the vesting
        # ledger already released that epoch's installments, so a re-run would lose that day's vector.
        self.last_scored_epoch = last_scored_epoch
        # The Herald epoch whose allocation this validator last got accepted on chain. Its only
        # writer is neurons/validator.py Validator.set_weights, after the extrinsic is included; its
        # only reader is Validator._has_weights_to_submit, which uses it so Bittensor's short
        # weight-update interval never resubmits an unchanged daily allocation. -1 means no accepted
        # submission is recorded in THIS file (a fresh or replaced file reads -1 too). It is not
        # evidence about on-chain weight-setting either way: watch the chain's LastUpdate for the
        # hotkey instead (scripts/watchdog.py).
        self.last_weight_epoch = last_weight_epoch

    @classmethod
    def fresh(cls) -> "HeraldState":
        return cls(CommitIndex(EPOCH_LEN), VestingLedger(VEST_EPOCHS), SlashLedger(), DisputeLedger())

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "commit_index": self.commit_index.to_dict(),
            "vesting": self.vesting.to_dict(),
            "slash": self.slash.to_dict(),
            "disputes": self.disputes.to_dict(),
            "pool_spent": self.pool_spent,
            "last_scored_epoch": self.last_scored_epoch,
            "last_weight_epoch": self.last_weight_epoch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HeraldState":
        if not isinstance(data, dict):
            raise ValueError(f"top level must be a JSON object, got {type(data).__name__}")
        version = schema_version_of(data)
        min_reader_schema_of(data)
        if version > SCHEMA_VERSION:
            bt.logging.warning(
                f"Herald state schema_version {version} is newer than this validator's "
                f"{SCHEMA_VERSION} (rolled back?); loading the fields this version understands"
            )
        for key in sorted(set(data) - _TOP_LEVEL_KEYS):
            bt.logging.warning(
                f"Ignoring unknown top-level Herald state key {key!r}; it is not kept on the next save"
            )
        # Value types are checked while loading, not left to the first scoring pass: a hand-repaired
        # "12" or null would otherwise load, then fail every pass behind forward()'s catch-all while
        # the validator kept running without scoring. Record fields are checked in rebuild_records.
        ci = _mapping(data, "commit_index")
        ve = _mapping(data, "vesting")
        slash = _mapping(data, "slash", required=True)
        first_seen = _check_values(_mapping(ci, "first_seen", where="commit_index."),
                                   "commit_index.first_seen", _is_int, "an integer block")
        until = _check_values(_mapping(slash, "until", where="slash."), "slash.until", _is_int,
                              "an integer epoch")
        pool_spent = _check_values(_mapping(data, "pool_spent"), "pool_spent", _is_number,
                                   "a number")
        epochs = {key: data.get(key, -1) for key in ("last_scored_epoch", "last_weight_epoch")}
        for key, value in epochs.items():
            if not _is_int(value):
                raise ValueError(f"{key} must be an integer, got {type(value).__name__}")
        # epoch_len / vest_epochs are consensus parameters: take them from config, never the
        # persisted file. A divisor that drifted across an upgrade would otherwise diverge
        # winner selection (commit_epoch) and installment size between validators.
        return cls(
            CommitIndex(EPOCH_LEN, first_seen),
            VestingLedger(VEST_EPOCHS, _mapping(ve, "entries", where="vesting.")),
            SlashLedger(until),
            DisputeLedger.from_dict(_mapping(data, "disputes")),
            pool_spent,
            epochs["last_scored_epoch"],
            epochs["last_weight_epoch"],
        )

    def save(self, path: str):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                # bittensor 10.x hands numpy scalars (int64 uids, float32 stakes) into the domain
                # objects; unbox them so persistence never dies on "int64 is not JSON serializable".
                json.dump(self.to_dict(), f, default=_json_np_safe)
                f.flush()
                os.fsync(f.fileno())  # bytes on disk before the rename makes them current
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        keep = _backup_keep()
        if keep and os.path.exists(path):
            _backup_previous(path)
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written state file
        if keep:
            _prune_backups(path, keep)

    @classmethod
    def load(cls, path: str) -> "HeraldState":
        for name in (ALLOW_FRESH_ON_CORRUPT_ENV, ALLOW_NEWER_SCHEMA_ENV):
            if _env_true(name):
                # Only a failed load acts on these, so an override left set after the emergency
                # would otherwise stay armed with no trace on a healthy start.
                bt.logging.warning(
                    f"{name} is set. Unset it in the validator's env file and recreate the "
                    f"container once the emergency it was set for is over."
                )
        if not os.path.exists(path):
            backups = backup_paths(path)
            if not backups:
                return cls.fresh()  # first boot
            return cls._fresh_beside_backups(path, backups)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            newer = _newer_schema(data)
        except Exception as e:
            return cls._fresh_after_unreadable(path, e)
        if newer:
            _admit_newer_schema(path, *newer)
        try:
            return cls.from_dict(data)
        except Exception as e:
            return cls._fresh_after_unreadable(path, e)

    @classmethod
    def _fresh_beside_backups(cls, path: str, backups: list) -> "HeraldState":
        """A missing file whose backups exist is lost state, not a first boot."""
        if not _env_true(ALLOW_FRESH_ON_CORRUPT_ENV):
            raise HeraldStateLoadError(path, FileNotFoundError(path), message=(
                f"Refusing to start: Herald state file {path} is missing but {len(backups)} "
                f"backup(s) of it exist (newest {backups[-1]}). It may have held in-flight "
                f"placements, and starting with an empty ledger would rotate those backups away. "
                f"Restore the newest backup to {path}, then restart the container. To start fresh "
                f"deliberately, set {ALLOW_FRESH_ON_CORRUPT_ENV}=true and recreate the container; "
                f"the backups are then renamed <backup>.kept, out of rotation, and never deleted."
            ))
        kept = []
        for backup in backups:
            try:
                os.replace(backup, backup + ".kept")
            except OSError as e:
                raise HeraldStateLoadError(path, e, message=(
                    f"Refusing to start: {ALLOW_FRESH_ON_CORRUPT_ENV} is set and Herald state file "
                    f"{path} is missing, but renaming its backup {backup} out of rotation failed "
                    f"({e}), so it will not start fresh and let rotation delete that backup."
                )) from e
            kept.append(backup + ".kept")
        bt.logging.error(
            f"{ALLOW_FRESH_ON_CORRUPT_ENV} is set and Herald state {path} is missing: STARTING WITH "
            f"AN EMPTY LEDGER. Its {len(kept)} backup(s) were renamed out of rotation so they are "
            f"never pruned (newest {kept[-1]}). Unset the variable and recreate the container once "
            f"the validator has saved a new state."
        )
        return cls.fresh()

    @classmethod
    def _fresh_after_unreadable(cls, path: str, e: Exception) -> "HeraldState":
        if not _env_true(ALLOW_FRESH_ON_CORRUPT_ENV):
            raise HeraldStateLoadError(path, e) from e
        preserved = _sibling(path, "corrupt")
        try:
            shutil.copy2(path, preserved)
        except OSError as copy_error:
            raise HeraldStateLoadError(
                path, e,
                f" {ALLOW_FRESH_ON_CORRUPT_ENV} is set, but copying the file aside to "
                f"{preserved} failed ({copy_error}), so it will not start fresh either.",
            ) from e
        bt.logging.error(
            f"{ALLOW_FRESH_ON_CORRUPT_ENV} is set: Herald state {path} could not be loaded "
            f"({type(e).__name__}: {e}). Preserved it as {preserved} and STARTING WITH AN EMPTY "
            f"LEDGER; no placement recorded in that file is loaded. Unset the variable and "
            f"recreate the container once the validator has saved a new state."
        )
        return cls.fresh()
