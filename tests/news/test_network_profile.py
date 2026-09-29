"""The release's built-in mainnet settings (herald/network_profile.py)."""

import os
import re
import subprocess
import sys

import pytest

from herald.network_profile import (
    MAINNET_DEFAULTS, apply_mainnet_defaults, targets_mainnet,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.parametrize("argv, env", [
    (["--netuid", "69", "--subtensor.network", "finney"], {}),
    (["--netuid=69", "--subtensor.network=finney"], {}),
    (["--netuid", "69"], {}),
    ([], {"NETUID": "69", "SUBTENSOR_NETWORK": "finney"}),
    ([], {"NETUID": " 69 "}),
    (["--netuid", "535", "--netuid", "69"], {}),
    (["--netuid", "69"], {"NETUID": "535"}),
])
def test_a_process_for_finney_netuid_69_targets_mainnet(argv, env):
    assert targets_mainnet(argv, env)


@pytest.mark.parametrize("argv, env", [
    ([], {}),
    (["-q", "-p", "no:cacheprovider"], {}),
    (["--netuid", "535", "--subtensor.network", "test"], {}),
    (["--netuid", "69", "--subtensor.network", "test"], {}),
    (["--netuid", "69", "--subtensor.network", "local"], {}),
    (["--netuid", "535"], {"NETUID": "69"}),
    ([], {"NETUID": "69", "SUBTENSOR_NETWORK": "test"}),
    (["--netuid", "69"], {"SUBTENSOR_NETWORK": "test"}),
    ([], {"NETUID": "sixty-nine"}),
    (["--netuid"], {}),
])
def test_any_other_target_or_no_netuid_is_not_mainnet(argv, env):
    assert not targets_mainnet(argv, env)


def test_mainnet_fills_unset_and_empty_settings_and_keeps_explicit_ones():
    env = {
        "HERALD_REGISTRY_AUTHORITY_HOTKEY": "",
        "HERALD_REGISTRY_PUBKEY": "   ",
        "HERALD_EPOCH_LAG": "7",
        "HERALD_RESULTS_ENDPOINT": "https://backend.example",
        "UNRELATED": "kept",
    }
    filled = apply_mainnet_defaults(["--netuid", "69"], env)

    assert "HERALD_EPOCH_LAG" not in filled and "HERALD_RESULTS_ENDPOINT" not in filled
    assert filled == [name for name in MAINNET_DEFAULTS
                      if name not in ("HERALD_EPOCH_LAG", "HERALD_RESULTS_ENDPOINT")]
    assert env["HERALD_EPOCH_LAG"] == "7"
    assert env["HERALD_RESULTS_ENDPOINT"] == "https://backend.example"
    assert env["HERALD_REGISTRY_AUTHORITY_HOTKEY"] == MAINNET_DEFAULTS["HERALD_REGISTRY_AUTHORITY_HOTKEY"]
    assert env["HERALD_REGISTRY_PUBKEY"] == MAINNET_DEFAULTS["HERALD_REGISTRY_PUBKEY"]
    assert env["UNRELATED"] == "kept"


def test_off_mainnet_nothing_is_filled():
    env = {"HERALD_REGISTRY_AUTHORITY_HOTKEY": ""}
    assert apply_mainnet_defaults(["--netuid", "535", "--subtensor.network", "test"], env) == []
    assert env == {"HERALD_REGISTRY_AUTHORITY_HOTKEY": ""}
    assert apply_mainnet_defaults([], {}) == []


def test_the_mainnet_values_are_well_formed():
    from bittensor_wallet import Keypair

    Keypair(ss58_address=MAINNET_DEFAULTS["HERALD_REGISTRY_AUTHORITY_HOTKEY"])
    # Each miner's own hotkey is paid, so no incentive hotkey is built in.
    assert "HERALD_INCENTIVE_HOTKEY" not in MAINNET_DEFAULTS
    for name in ("HERALD_REGISTRY_PUBKEY", "HERALD_BRIEFS_PUBKEY"):
        assert re.fullmatch(r"[0-9a-f]{64}", MAINNET_DEFAULTS[name])
    for name in ("HERALD_RESULTS_ENDPOINT", "HERALD_REGISTRY_ENDPOINT"):
        assert MAINNET_DEFAULTS[name] == "https://api.heraldmedia.ai"
    assert MAINNET_DEFAULTS["HERALD_BRIEFS_ENDPOINT"] == (
        "https://api.heraldmedia.ai/api/v2/validator/briefs")
    assert MAINNET_DEFAULTS["HERALD_REQUIRE_SIGNED_REGISTRY"] == "true"
    assert MAINNET_DEFAULTS["HERALD_REQUIRE_SIGNED_BRIEFS"] == "true"


def test_the_mainnet_epoch_lag_keeps_the_fleet_epoch_numbering():
    lag = int(MAINNET_DEFAULTS["HERALD_EPOCH_LAG"])
    assert (9044797 - lag) // 7200 == 1258
    assert (9044796 - lag) // 7200 == 1257


def test_mainnet_settings_satisfy_the_production_preflight_trust_anchors(tmp_path):
    from herald.production import validator_environment_errors

    env = {"HERALD_PRODUCTION": "true", "HERALD_REGISTRY_PATH": str(tmp_path / "missing.json")}
    apply_mainnet_defaults(["--netuid", "69"], env)
    errors = validator_environment_errors(env, network="finney", netuid=69)

    for setting in ("HERALD_REGISTRY_PUBKEY",
                    "HERALD_REGISTRY_AUTHORITY_HOTKEY", "HERALD_BRIEFS_PUBKEY",
                    "HERALD_RESULTS_ENDPOINT", "HERALD_BRIEFS_ENDPOINT", "signed registry",
                    "signed briefs", "finney",
                    "netuid 69"):
        assert not any(setting in error for error in errors), (setting, errors)
    # What stays the operator's: credentials, keys, the registry file and the pinned fingerprint.
    assert any("HERALD_EXPECTED_CONSENSUS_FP" in error for error in errors)
    assert any("HERALD_RESULTS_TOKEN" in error for error in errors)


def _run_herald(args, extra_env=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "PYTHONPATH": REPO_ROOT,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra_env or {})
    result = subprocess.run([sys.executable, *args], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip().splitlines()[-1]


def test_a_validator_relying_on_the_release_computes_the_same_fingerprint_as_one_setting_it():
    built_in = _run_herald(["-m", "herald.production", "fingerprint",
                            "--netuid", "69", "--subtensor.network", "finney"])
    explicit = _run_herald(["-m", "herald.production", "fingerprint"], dict(MAINNET_DEFAULTS))
    plain = _run_herald(["-m", "herald.production", "fingerprint"])

    assert re.fullmatch(r"[0-9a-f]{16}", built_in)
    assert built_in == explicit
    assert plain != built_in


def test_the_validator_config_takes_the_mainnet_values_for_its_target():
    probe = ("import os; from herald.validator.utils import config as c; "
             "print(os.environ.get('HERALD_REGISTRY_AUTHORITY_HOTKEY') or '-', c.HERALD_EPOCH_LAG, "
             "','.join(c.MAINNET_DEFAULTS_APPLIED) or '-')")

    mainnet = _run_herald(["-c", probe, "--netuid", "69", "--subtensor.network", "finney"])
    assert mainnet == (f"{MAINNET_DEFAULTS['HERALD_REGISTRY_AUTHORITY_HOTKEY']} "
                       f"{MAINNET_DEFAULTS['HERALD_EPOCH_LAG']} {','.join(MAINNET_DEFAULTS)}")

    testnet = _run_herald(["-c", probe, "--netuid", "535", "--subtensor.network", "test"])
    assert testnet == "- 10 -"

    overridden = _run_herald(["-c", probe, "--netuid", "69"],
                             {"HERALD_REGISTRY_AUTHORITY_HOTKEY": "5Override", "HERALD_EPOCH_LAG": "3"})
    assert overridden.startswith("5Override 3 ")
