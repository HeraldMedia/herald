"""Built-in settings for the Herald subnet on mainnet (finney, netuid 69).

A process started for finney netuid 69 takes these values for every setting below that its
environment leaves unset or empty, so a mainnet validator needs only its wallet, its API keys and
its results credential: pulling a release and restarting picks up that release's values. An
explicit, non-empty environment value always wins, which is how a testnet or local run keeps its
own. Every value here is public.

The target is read from the command line (--netuid, --subtensor.network), falling back to the
NETUID and SUBTENSOR_NETWORK environment variables; the network defaults to finney, as it does in
bittensor. Without a netuid nothing is applied, so tests and tools that name no subnet see the
plain defaults.

Standard library only, with no herald imports: scripts/watchdog.py loads this file by path so that
it never imports the herald package.
"""

import os
import sys

MAINNET_NETWORK = "finney"
MAINNET_NETUID = 69

MAINNET_DEFAULTS = {
    # Aligns evaluation epochs fleet-wide: epoch 1258 began at block 9044797.
    "HERALD_EPOCH_LAG": "-12803",
    # The canonical backend: submissions feed, results, briefs and the active registry edition.
    "HERALD_RESULTS_ENDPOINT": "https://api.heraldmedia.ai",
    "HERALD_BRIEFS_ENDPOINT": "https://api.heraldmedia.ai/api/v2/validator/briefs",
    "HERALD_REGISTRY_ENDPOINT": "https://api.heraldmedia.ai",
    # Trust anchors.
    "HERALD_REGISTRY_PUBKEY": "9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6",
    "HERALD_REGISTRY_AUTHORITY_HOTKEY": "5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5",
    "HERALD_REQUIRE_SIGNED_REGISTRY": "true",
    "HERALD_BRIEFS_PUBKEY": "a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a",
    "HERALD_REQUIRE_SIGNED_BRIEFS": "true",
}


def _flag(argv, name: str):
    """The last value given for `name` as `name value` or `name=value`, as argparse reads it."""
    value = None
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith(name + "="):
            value = arg[len(name) + 1:]
    return value


def targets_mainnet(argv=None, env=None) -> bool:
    """Whether this process runs for finney netuid 69."""
    argv = sys.argv[1:] if argv is None else list(argv)
    env = os.environ if env is None else env
    netuid = _flag(argv, "--netuid") or env.get("NETUID") or ""
    network = _flag(argv, "--subtensor.network") or env.get("SUBTENSOR_NETWORK") or MAINNET_NETWORK
    try:
        netuid = int(str(netuid).strip())
    except ValueError:
        return False
    return netuid == MAINNET_NETUID and str(network).strip() == MAINNET_NETWORK


def apply_mainnet_defaults(argv=None, env=None) -> list:
    """Fill every unset or empty MAINNET_DEFAULTS setting in `env` when the process targets mainnet.

    Returns the names it filled, in MAINNET_DEFAULTS order; an empty list off mainnet.
    """
    env = os.environ if env is None else env
    if not targets_mainnet(argv, env):
        return []
    filled = []
    for name, value in MAINNET_DEFAULTS.items():
        if not str(env.get(name) or "").strip():
            env[name] = value
            filled.append(name)
    return filled
