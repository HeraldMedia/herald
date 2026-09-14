"""Rebuild persisted dataclass records tolerantly, so the state file survives a version rollback.

``VestEntry(**v)`` raises ``TypeError`` on any key the dataclass does not declare. A field added by a
newer Herald release would then crash an older validator's load, or, behind a catch-all, silently
empty its ledger. Unknown keys are dropped instead and reported at WARNING.
Missing *required* fields still raise, so a genuinely malformed record fails the load and the
validator refuses to start rather than inventing values. So does a wrong-typed value in a field
declared int, float or dict: a hand-repaired "400" or null would otherwise load, then break
arithmetic on every scoring pass behind forward()'s catch-all.
"""

from dataclasses import fields
from typing import Dict, Type, TypeVar

import bittensor as bt

T = TypeVar("T")

# Declared field types a persisted value must match. Strings are not checked: nothing computes with them.
_CHECKED_TYPES = (int, float, dict)


def _has_declared_type(value, declared) -> bool:
    if isinstance(value, bool):
        return False  # JSON true/false is never a number or an object here
    if declared is float:
        return isinstance(value, (int, float))  # vesting.start accepts whole-dollar ints
    return isinstance(value, declared)


def rebuild_records(record_type: Type[T], records, *, label: str) -> Dict[str, T]:
    """Return ``{key: record_type(**known_fields)}`` for a persisted ``{key: dict}`` mapping."""
    allowed = {f.name for f in fields(record_type)}
    checked = [f for f in fields(record_type) if f.type in _CHECKED_TYPES]
    rebuilt: Dict[str, T] = {}
    dropped: Dict[str, list] = {}
    for key, raw in (records or {}).items():
        if not isinstance(raw, dict):
            raise TypeError(f"{label} {key!r} must be a JSON object, got {type(raw).__name__}")
        for name in raw:
            if name not in allowed:
                dropped.setdefault(name, []).append(key)
        for f in checked:
            if f.name in raw and not _has_declared_type(raw[f.name], f.type):
                raise TypeError(
                    f"{label} {key!r} field {f.name!r} must be {f.type.__name__}, "
                    f"got {type(raw[f.name]).__name__}"
                )
        rebuilt[key] = record_type(**{k: v for k, v in raw.items() if k in allowed})
    for name in sorted(dropped):
        keys = dropped[name]
        sample = ", ".join(repr(k) for k in keys[:3]) + (", ..." if len(keys) > 3 else "")
        bt.logging.warning(
            f"Dropping unknown {label} field {name!r} from {len(keys)} record(s) ({sample}); it was "
            "probably written by a newer Herald version and is not kept on the next save"
        )
    return rebuilt
