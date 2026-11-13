"""Herald's command-line configuration: argparse into a nested attribute tree.

``parse_config(parser)`` turns ``--wallet.name x --neuron.epoch_length 5`` into a ``Config`` read
as ``config.wallet.name`` and ``config.neuron.epoch_length``. The behaviour matches the
``bittensor.Config`` Herald used before:

- unknown arguments are ignored (``--strict`` makes them an error), and a bare ``--name`` naming a
  known option sets it to True;
- ``--config <file.yaml>`` supplies defaults from a YAML file;
- ``config.is_set("wallet.name")`` says whether a flag was given on the command line;
- an attribute that was never set reads as None;
- ``str(config)`` is the YAML dump the neuron logs at startup.

``add_wallet_args``, ``add_subtensor_args`` and ``add_axon_args`` add the flags bittensor used to
add, with the same names, defaults and BT_* environment defaults. The ``--axon.*`` and
``--subtensor._mock`` flags are accepted so existing command lines keep parsing, and are ignored.
"""

import argparse
import copy
import os
import sys

import yaml

FINNEY_ENDPOINT = "wss://entrypoint-finney.opentensor.ai:443"
DEFAULT_NETWORK = "finney"

# Flags accepted for compatibility and dropped from the parsed tree.
IGNORED_NAMESPACES = ("axon",)
IGNORED_KEYS = (("subtensor", "_mock"),)


class Config(dict):
    """A dict whose keys are also attributes; nested namespaces are Configs."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        object.__setattr__(self, "_is_set", {})
        for key, value in dict(*args, **kwargs).items():
            self[key] = _wrap(value)

    def __getattr__(self, key):
        if key.startswith("__") and key.endswith("__"):
            raise AttributeError(key)
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = _wrap(value)

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __deepcopy__(self, memo):
        new = Config()
        memo[id(self)] = new
        for key, value in self.items():
            new[copy.deepcopy(key, memo)] = copy.deepcopy(value, memo)
        object.__setattr__(new, "_is_set", copy.deepcopy(self._is_set, memo))
        return new

    def is_set(self, name: str) -> bool:
        """Whether `name` (e.g. "subtensor.network") was given on the command line."""
        return bool(self._is_set.get(name, False))

    def merge(self, other: "Config") -> None:
        """Merge `other` into this config, its values winning, namespace by namespace."""
        _merge_into(self, other)
        self._is_set.update(getattr(other, "_is_set", {}) or {})

    def to_dict(self) -> dict:
        return {key: value.to_dict() if isinstance(value, Config) else value
                for key, value in self.items()}

    toDict = to_dict

    def __str__(self) -> str:
        return "\n" + yaml.dump(_public(self.to_dict()), sort_keys=False, default_flow_style=False)

    __repr__ = __str__


def _wrap(value):
    return Config(value) if isinstance(value, dict) and not isinstance(value, Config) else value


def _merge_into(target: Config, other) -> None:
    for key, value in other.items():
        if isinstance(target.get(key), Config) and isinstance(value, Config):
            _merge_into(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _public(data):
    if isinstance(data, dict):
        return {key: _public(value) for key, value in data.items() if not str(key).startswith("__")}
    return data


# --- flags -------------------------------------------------------------------------------------

def _add(parser, *args, **kwargs):
    try:
        parser.add_argument(*args, **kwargs)
    except argparse.ArgumentError:
        pass  # already added to this parser


def add_wallet_args(parser: argparse.ArgumentParser, prefix: str = None):
    p = "" if prefix is None else prefix + "."
    _add(parser, f"--{p}wallet.name", required=False,
         default=os.getenv("BT_WALLET_NAME") or "default",
         help="The name of the wallet to unlock for running Bittensor (name of the folder under "
              "the wallet path).")
    _add(parser, f"--{p}wallet.hotkey", required=False,
         default=os.getenv("BT_WALLET_HOTKEY") or "default",
         help="The name of the wallet's hotkey.")
    _add(parser, f"--{p}wallet.path", required=False,
         default=os.getenv("BT_WALLET_PATH") or os.path.join(
             os.path.expanduser("~"), ".bittensor", "wallets"),
         help="The path to your Bittensor wallets.")


def add_subtensor_args(parser: argparse.ArgumentParser, prefix: str = None):
    p = "" if prefix is None else prefix + "."
    _add(parser, f"--{p}subtensor.network", type=str,
         default=os.getenv("BT_SUBTENSOR_NETWORK") or DEFAULT_NETWORK,
         help="The subtensor network: finney (main network), test, archive, local, or a "
              "WebSocket endpoint URL.")
    _add(parser, f"--{p}subtensor.chain_endpoint", type=str,
         default=os.getenv("BT_SUBTENSOR_CHAIN_ENDPOINT") or FINNEY_ENDPOINT,
         help="A chain endpoint; used only when --subtensor.network is empty.")
    _add(parser, f"--{p}subtensor._mock", default=False, type=bool, help=argparse.SUPPRESS)


def add_axon_args(parser: argparse.ArgumentParser, prefix: str = None):
    """The --axon.* flags. Herald serves no axon: they are accepted and ignored."""
    p = "" if prefix is None else prefix + "."
    _add(parser, f"--{p}axon.port", type=int, default=None, help=argparse.SUPPRESS)
    _add(parser, f"--{p}axon.ip", type=str, default=None, help=argparse.SUPPRESS)
    _add(parser, f"--{p}axon.external_port", type=int, default=None, help=argparse.SUPPRESS)
    _add(parser, f"--{p}axon.external_ip", type=str, default=None, help=argparse.SUPPRESS)
    _add(parser, f"--{p}axon.max_workers", type=int, default=None, help=argparse.SUPPRESS)


def _add_config_args(parser: argparse.ArgumentParser):
    _add(parser, "--config", type=str, default=False,
         help="If set, defaults are overridden by the passed YAML file.")
    _add(parser, "--strict", action="store_true", default=False,
         help="If flagged, unknown arguments are an error.")
    _add(parser, "--no_version_checking", action="store_true", default=False,
         help=argparse.SUPPRESS)


# --- parsing -----------------------------------------------------------------------------------

def _parse(parser: argparse.ArgumentParser, args: list, strict: bool) -> dict:
    if strict:
        return vars(parser.parse_args(args))
    result, unknown = parser.parse_known_args(args)
    values = vars(result)
    for arg in unknown:
        if arg.startswith("--") and arg[2:] in values:
            values[arg[2:]] = True
    return values


def _tree(params: dict) -> Config:
    config = Config()
    for key, value in params.items():
        node = config
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), Config):
                node[part] = Config()
            node = node[part]
        node[parts[-1]] = value
    for name in IGNORED_NAMESPACES:
        config.pop(name, None)
    for namespace, key in IGNORED_KEYS:
        if isinstance(config.get(namespace), Config):
            config[namespace].pop(key, None)
    return config


def parse_config(parser: argparse.ArgumentParser, args: list = None, strict: bool = False) -> Config:
    """Parse `args` (default ``sys.argv[1:]``) with `parser` into a Config."""
    _add_config_args(parser)
    args = sys.argv[1:] if args is None else list(args)
    first = _parse(parser, args, strict=False)
    strict = strict or bool(first.get("strict"))
    path = first.get("config")
    if path:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            defaults = yaml.safe_load(f) or {}
        print(f"Loading config from: {path}")
        parser.set_defaults(**defaults)
    config = _tree(_parse(parser, args, strict))

    explicit = copy.deepcopy(parser)
    for action in explicit._actions:
        action.default = argparse.SUPPRESS
    given = _parse(explicit, args, strict=False)
    object.__setattr__(config, "_is_set", {key: True for key in given})
    return config
