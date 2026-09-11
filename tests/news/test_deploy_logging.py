"""The shipped validator launch configs start at INFO (bittensor's default is WARNING)."""

import argparse
import os
import re

import bittensor as bt
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGGING_ARG = "${HERALD_VALIDATOR_LOGGING:---logging.info}"


def test_compose_validator_starts_with_info_logging_overridable_by_env():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    command = compose["services"]["validator"]["command"]

    assert command[:3] == ["python", "-u", "neurons/validator.py"]
    assert command.count(LOGGING_ARG) == 1
    assert not any(arg.startswith("--logging.") for arg in command)
    name, default = re.fullmatch(r"\$\{(\w+):-(.+)\}", LOGGING_ARG).groups()
    assert (name, default) == ("HERALD_VALIDATOR_LOGGING", "--logging.info")


def test_logging_flags_are_accepted_by_the_pinned_bittensor():
    parser = argparse.ArgumentParser()
    bt.logging.add_args(parser)
    for flag in ("--logging.info", "--logging.debug", "--logging.trace"):
        assert getattr(parser.parse_args([flag]), flag[2:]) is True
    assert getattr(parser.parse_args([]), "logging.info") is False  # no flag: WARNING


def test_quorum_rollout_validators_also_default_to_info_logging():
    with open(os.path.join(ROOT, "scripts", "rollout_validator_quorum.sh"), encoding="utf-8") as f:
        assert f.read().count(LOGGING_ARG) == 1
