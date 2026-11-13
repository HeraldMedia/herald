"""herald.sdk.config and herald.utils.config: every shipped command line parses as it did."""

import os
import re
import shlex
import sys

import pytest
import yaml

from herald.base.validator import BaseValidatorNeuron
from herald.sdk import chain
from herald.sdk import config as sdk_config
from herald.utils import config as herald_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _validator_config(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["neurons/validator.py", *argv])
    return herald_config.config(BaseValidatorNeuron)


def _compose_validator_args():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        command = yaml.safe_load(f)["services"]["validator"]["command"]
    # ${NAME:-default} -> default, as compose substitutes it when NAME is unset.
    return [re.sub(r"\$\{\w+:-(.*)\}", r"\1", arg) for arg in command[3:]]


def test_the_compose_validator_command_parses_to_the_same_settings(monkeypatch):
    args = _compose_validator_args()
    assert args == ["--wallet.name", "default", "--wallet.hotkey", "default", "--netuid", "69",
                    "--subtensor.network", "finney", "--neuron.disable_auto_update",
                    "--logging.info"]
    config = _validator_config(monkeypatch, args)

    assert (config.wallet.name, config.wallet.hotkey) == ("default", "default")
    assert config.netuid == 69
    assert config.subtensor.network == "finney"
    assert config.neuron.disable_auto_update is True
    assert config.logging.info is True and config.logging.debug is False
    assert config.neuron.name == "validator"
    assert config.neuron.epoch_length == 100
    assert config.neuron.disable_set_weights is False
    assert config.neuron.moving_average_alpha == 0.6
    assert config.is_set("wallet.name") and config.is_set("subtensor.network")
    assert not config.is_set("subtensor.chain_endpoint")
    assert chain.network_from_config(config) == "finney"


def test_axon_flags_from_the_entrypoint_are_accepted_and_ignored(monkeypatch):
    config = _validator_config(monkeypatch, [
        "--netuid", "69", "--axon.external_ip", "203.0.113.5", "--axon.external_port", "8091",
        "--axon.port", "8091",
    ])
    assert config.netuid == 69
    assert "axon" not in config
    assert config.axon is None


def test_unknown_flags_are_ignored_unless_strict(monkeypatch):
    config = _validator_config(monkeypatch, ["--netuid", "70", "--dev_mode", "--blacklist.min_stake", "5"])
    assert config.netuid == 70
    with pytest.raises(SystemExit):
        _validator_config(monkeypatch, ["--strict", "--dev_mode"])


def test_scripts_and_rollout_command_lines_parse(monkeypatch, tmp_path):
    flags = [
        # scripts/run_validator.sh and run_validator_disable_weights.sh
        "--netuid 69 --subtensor.chain_endpoint wss://entrypoint-finney.opentensor.ai:443 "
        "--subtensor.network finney --wallet.name w --wallet.hotkey h --logging.debug "
        "--neuron.disable_auto_update --neuron.disable_set_weights",
        # scripts/rollout_validator_quorum.sh
        f"--netuid 69 --subtensor.network finney --wallet.name w --wallet.hotkey h "
        f"--neuron.name herald-netuid69-v3-1 --neuron.disable_auto_update "
        f"--neuron.dont_save_events --logging.info --logging.logging_dir {tmp_path}",
        "--logging.trace --neuron.epoch_length 5 --wallet.path /tmp/wallets",
    ]
    first = _validator_config(monkeypatch, shlex.split(flags[0]))
    assert first.logging.debug is True and first.neuron.disable_set_weights is True
    assert first.subtensor.chain_endpoint == "wss://entrypoint-finney.opentensor.ai:443"
    second = _validator_config(monkeypatch, shlex.split(flags[1]))
    assert second.neuron.name == "herald-netuid69-v3-1" and second.neuron.dont_save_events is True
    assert second.logging.logging_dir == str(tmp_path)
    third = _validator_config(monkeypatch, shlex.split(flags[2]))
    assert third.logging.trace is True and third.neuron.epoch_length == 5
    assert third.wallet.path == "/tmp/wallets"


def test_environment_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BT_WALLET_PATH", str(tmp_path / "wallets"))
    monkeypatch.setenv("BT_WALLET_NAME", "envwallet")
    monkeypatch.setenv("BT_SUBTENSOR_NETWORK", "test")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BT_LOGGING_LOGGING_DIR", raising=False)
    monkeypatch.delenv("READ_ONLY", raising=False)
    config = _validator_config(monkeypatch, [])
    assert config.wallet.path == str(tmp_path / "wallets")
    assert config.wallet.name == "envwallet" and config.wallet.hotkey == "default"
    assert config.subtensor.network == "test"
    assert config.logging.logging_dir == str(tmp_path / ".bittensor" / "miners")


def test_the_neuron_state_directory_is_where_it_was(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BT_LOGGING_LOGGING_DIR", raising=False)
    monkeypatch.delenv("READ_ONLY", raising=False)
    config = _validator_config(monkeypatch, [
        "--wallet.name", "herald", "--wallet.hotkey", "v1", "--netuid", "69",
        "--neuron.dont_save_events"])
    herald_config.check_config(BaseValidatorNeuron, config)
    assert config.neuron.full_path == str(
        tmp_path / ".bittensor" / "miners" / "herald" / "v1" / "netuid69" / "validator")
    assert os.path.isdir(config.neuron.full_path)


def test_a_yaml_config_file_supplies_defaults(monkeypatch, tmp_path):
    path = tmp_path / "validator.yaml"
    path.write_text(yaml.safe_dump({"netuid": 535, "neuron.epoch_length": 7}))
    config = _validator_config(monkeypatch, ["--config", str(path), "--neuron.epoch_length", "9"])
    assert config.netuid == 535
    assert config.neuron.epoch_length == 9  # the command line still wins


def test_merge_deepcopy_and_dump_keep_values_and_explicit_flags(monkeypatch):
    import copy

    base = _validator_config(monkeypatch, ["--netuid", "69", "--wallet.name", "w"])
    other = _validator_config(monkeypatch, ["--netuid", "69", "--wallet.name", "w"])
    other.merge(copy.deepcopy(base))
    assert other.wallet.name == "w" and other.is_set("wallet.name")
    dumped = yaml.safe_load(str(other))
    assert dumped["wallet"]["name"] == "w" and dumped["netuid"] == 69
    assert other.neuron.missing_attribute is None


@pytest.mark.parametrize("argv, network", [
    ([], "finney"),
    (["--subtensor.chain_endpoint", "wss://node.example:443"], "finney"),
    (["--subtensor.network", "test", "--subtensor.chain_endpoint", "wss://node.example:443"], "test"),
    (["--subtensor.network", "", "--subtensor.chain_endpoint", "wss://node.example:443"],
     "wss://node.example:443"),
    (["--subtensor.network", "wss://node.example:9944"], "wss://node.example:9944"),
])
def test_network_selection_matches_the_previous_rules(monkeypatch, argv, network):
    import argparse

    monkeypatch.delenv("BT_SUBTENSOR_NETWORK", raising=False)
    monkeypatch.delenv("BT_SUBTENSOR_CHAIN_ENDPOINT", raising=False)
    parser = argparse.ArgumentParser()
    sdk_config.add_subtensor_args(parser)
    config = sdk_config.parse_config(parser, argv)
    assert chain.network_from_config(config) == network
