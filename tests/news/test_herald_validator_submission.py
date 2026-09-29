import importlib.util
import os
import re
from types import SimpleNamespace

import numpy as np
import pytest

import neurons.validator as valmod
from herald.base.neuron import BaseNeuron
from herald.base.validator import BaseValidatorNeuron
from herald.validator.news.state import (
    ALLOW_FRESH_ON_CORRUPT_ENV,
    HeraldState,
    HeraldStateLoadError,
)
from herald.validator.utils import config as cfg
from herald.validator.utils.consensus import consensus_fingerprint, consensus_params
from neurons.validator import Validator

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MINER = "hkMinerA"
UID = 1  # this validator
UID_MINER = 2
HOTKEYS = ["hkOwner", "hkValidator", MINER, "hkMinerB"]
# The latest epoch paid the miner at UID 2 a quarter of the day and burned the rest.
MINER_AND_BURN = (0.75, 0.0, 0.25, 0.0)


@pytest.fixture(autouse=True)
def _submission_env(monkeypatch):
    monkeypatch.setattr(valmod, "WEIGHT_RESUBMIT_BLOCKS", 180)
    monkeypatch.setattr(Validator, "block", property(lambda self: self._block), raising=False)


@pytest.fixture
def logs(monkeypatch):
    lines = []
    for level in ("info", "warning", "error"):
        monkeypatch.setattr(valmod.bt.logging, level,
                            lambda msg="", *a, _level=level, **k: lines.append((_level, str(msg))))
    return lines


def _validator(tmp_path, *, scored_epoch=10, weight_epoch=10, scores=MINER_AND_BURN,
               block=1000, last_update=700, pending=False, min_allowed=1, epoch_length=100,
               disable_set_weights=False, hotkeys=HOTKEYS):
    """A validator on a fake chain: the real base-class gates run and every extrinsic is recorded.

    ``last_update`` is the chain's weight record for this validator's uid, so the record is
    ``block - last_update`` blocks old.
    """
    validator = object.__new__(Validator)
    validator.step = 1
    validator.uid = UID
    validator.config = SimpleNamespace(
        netuid=69,
        neuron=SimpleNamespace(full_path=str(tmp_path), epoch_length=epoch_length,
                               disable_set_weights=disable_set_weights),
    )
    validator.scores = np.asarray(scores, dtype=np.float32)
    validator.herald_state = HeraldState.fresh()
    validator.herald_state.last_scored_epoch = scored_epoch
    validator.herald_state.last_weight_epoch = weight_epoch
    validator.herald_state.weight_hotkeys = {UID_MINER: MINER}
    record = np.zeros(len(hotkeys), dtype=np.int64)
    record[UID] = last_update
    validator.metagraph = SimpleNamespace(n=len(hotkeys), uids=np.arange(len(hotkeys)),
                                          hotkeys=list(hotkeys), last_update=record)
    validator.wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="hkValidator"))
    chain = SimpleNamespace(submissions=[], commit_reads=0)

    def timelocked_commits(netuid):
        chain.commit_reads += 1
        return [("hkValidator", block - 10, "encrypted", 1)] if pending else []

    def set_weights(**kwargs):
        chain.submissions.append(kwargs)
        return True, "included"

    validator.subtensor = SimpleNamespace(
        commit_reveal_enabled=lambda netuid: True,
        get_timelocked_weight_commits=timelocked_commits,
        min_allowed_weights=lambda netuid: min_allowed,
        set_weights=set_weights,
    )
    validator._block = block
    validator.check_registered = lambda: None
    validator.should_sync_metagraph = lambda: False
    validator.save_state = lambda: None
    return validator, chain


def _vectors(chain):
    return [(kwargs["uids"], kwargs["weights"]) for kwargs in chain.submissions]


def _messages(logs, level):
    return [msg for lvl, msg in logs if lvl == level]


def _submission_lines(logs):
    return [msg for msg in _messages(logs, "info")
            if msg.startswith(("Submitting", "Re-submitting"))]


# --- block-cadence submission -----------------------------------------------------------------------

def test_a_submitted_epoch_is_resubmitted_once_the_chain_record_is_stale(tmp_path, logs):
    # Already submitted in this epoch, the record is 300 blocks old and no commit is pending.
    validator, chain = _validator(tmp_path, weight_epoch=10, block=1000, last_update=700)

    validator.sync()

    assert _vectors(chain) == [([0, UID_MINER], [65535, 21845])]
    assert _submission_lines(logs) == [
        "Re-submitting Herald epoch 10 weights: the chain record for uid 1 is 300 blocks old (>= 180)"
    ]
    assert chain.commit_reads == 1
    assert validator.herald_state.last_weight_epoch == 10


def test_a_newly_scored_epoch_is_submitted_under_the_same_rule(tmp_path, logs):
    validator, chain = _validator(tmp_path, weight_epoch=9, block=1000, last_update=700)

    validator.sync()

    assert _vectors(chain) == [([0, UID_MINER], [65535, 21845])]
    assert _submission_lines(logs) == [
        "Submitting Herald epoch 10 weights: the chain record for uid 1 is 300 blocks old (>= 180)"
    ]
    assert validator.herald_state.last_weight_epoch == 10
    assert HeraldState.load(str(tmp_path / "herald_state.json")).last_weight_epoch == 10


@pytest.mark.parametrize("age, submitted", [(120, False), (179, False), (180, True)])
def test_a_fresh_chain_record_skips_with_the_fresh_reason(tmp_path, logs, age, submitted):
    validator, chain = _validator(tmp_path, block=1000, last_update=1000 - age)

    validator.sync()

    assert bool(chain.submissions) is submitted
    fresh = f"Weights for uid 1 are {age} blocks old (< 180); skipping resubmission"
    assert (fresh in _messages(logs, "info")) is not submitted
    if not submitted:
        assert chain.commit_reads == 0 and _submission_lines(logs) == []


def test_the_interval_the_validator_applies_is_the_configured_one(tmp_path, monkeypatch, logs):
    monkeypatch.setattr(valmod, "WEIGHT_RESUBMIT_BLOCKS", 360)
    validator, chain = _validator(tmp_path, block=1000, last_update=700)

    validator.sync()

    assert chain.submissions == []
    fresh = "Weights for uid 1 are 300 blocks old (< 360); skipping resubmission"
    assert fresh in _messages(logs, "info")


def test_a_pending_commit_skips_resubmission(tmp_path, logs):
    validator, chain = _validator(tmp_path, block=1000, last_update=700, pending=True)

    validator.sync()

    assert chain.submissions == [] and chain.commit_reads == 1
    pending = "Weight commitment pending automatic reveal; skipping resubmission"
    assert pending in _messages(logs, "info")
    assert _submission_lines(logs) == []


def test_a_burn_only_vector_is_resubmitted(tmp_path, logs):
    validator, chain = _validator(tmp_path, scores=(1.0, 0.0, 0.0, 0.0), block=1000,
                                  last_update=700)

    validator.sync()
    validator.metagraph.last_update[UID] = 1000  # revealed
    validator._block = 1180
    validator.sync()

    assert _vectors(chain) == [([0], [65535]), ([0], [65535])]
    assert len(_submission_lines(logs)) == 2 and _messages(logs, "error") == []


@pytest.mark.parametrize("min_allowed", [1, 2])
@pytest.mark.parametrize("scores, vector", [
    ((0.5, 0.0, 0.0, 0.5), ([0], [65535])),                # a registered miner the epoch did not score
    ((0.0, 0.3, 0.0, 0.0), ([0], [65535])),                # this validator's own UID
    ((0.4, 0.0, 0.3, 0.3), ([0, UID_MINER], [65535, 28086])),  # the scored miner and another UID
])
def test_a_stored_score_outside_uid_zero_and_the_scored_miners_moves_to_uid_zero(
        tmp_path, logs, scores, vector, min_allowed):
    validator, chain = _validator(tmp_path, scores=scores, min_allowed=min_allowed)

    validator.sync()

    assert any(msg.startswith("WEIGHT_VECTOR_BURN reason=hotkey_changed")
               for msg in _messages(logs, "warning"))
    if len(vector[0]) >= min_allowed:
        assert _vectors(chain) == [vector]
    else:
        assert chain.submissions == []  # refused
        assert _messages(logs, "error") == [
            "WEIGHT_VECTOR_REFUSED reason=below_min_allowed_weights uids=[0] min_allowed_weights=2"
        ]


@pytest.mark.parametrize("hotkeys", [
    ["hkOwner", "hkValidator", "hkReplacement", MINER],    # the miner re-registered at UID 3
    ["hkOwner", "hkValidator", "hkReplacement", "hkMinerB"],  # it is no longer registered
])
def test_a_resubmission_after_a_scored_miners_uid_changed_hands_burns_its_weight(tmp_path, logs,
                                                                               hotkeys):
    validator, chain = _validator(tmp_path, scores=MINER_AND_BURN)
    validator.metagraph.hotkeys = list(hotkeys)

    validator.sync()

    assert _vectors(chain) == [([0], [65535])]
    assert any(msg.startswith("WEIGHT_VECTOR_BURN reason=hotkey_changed")
               for msg in _messages(logs, "warning"))


def test_min_allowed_weights_above_the_vector_length_is_refused_on_every_resubmission(tmp_path, logs):
    validator, chain = _validator(tmp_path, weight_epoch=9, min_allowed=3)

    validator.sync()
    validator.sync()

    assert chain.submissions == []
    assert _messages(logs, "error") == [
        "WEIGHT_VECTOR_REFUSED reason=below_min_allowed_weights uids=[0, 2] min_allowed_weights=3"
    ] * 2
    assert validator.herald_state.last_weight_epoch == 9


@pytest.mark.parametrize("setup", [
    lambda v: setattr(v.config.neuron, "disable_set_weights", True),
    lambda v: setattr(v.config.neuron, "epoch_length", 400),  # the record is 300 blocks old
    lambda v: setattr(v, "step", 0),
])
def test_the_base_class_gates_still_hold_back_a_stale_record(tmp_path, logs, setup):
    validator, chain = _validator(tmp_path, block=1000, last_update=700)
    setup(validator)

    validator.sync()

    assert chain.submissions == [] and chain.commit_reads == 0
    assert _submission_lines(logs) == []


@pytest.mark.parametrize("change, message", [
    (dict(scored_epoch=-1, weight_epoch=-1),
     "No Herald epoch has been scored yet; skipping weight submission"),
    (dict(scores=(0.0, 0.0, 0.0, 0.0)),
     "Herald epoch 10 has no rewarded miners; skipping weight submission"),
    (dict(last_update=900),
     "Weights for uid 1 are 100 blocks old (< 180); skipping resubmission"),
])
def test_nothing_to_submit_never_reaches_the_pending_commit_check(
        tmp_path, monkeypatch, logs, change, message):
    # A failing RPC check retries with sleeps, counts a suppression and eventually logs
    # WEIGHT_SUBMISSION_STALLED. None of that may happen when Herald would not submit.
    monkeypatch.setattr(BaseNeuron, "should_set_weights", lambda self: True)
    validator, chain = _validator(tmp_path, **change)

    assert validator.should_set_weights() is False

    assert message in _messages(logs, "info")
    assert chain.commit_reads == 0
    assert getattr(validator, "_suppressed_weight_submissions", 0) == 0


def test_an_unreadable_weight_record_skips_instead_of_submitting_blind(tmp_path, monkeypatch, logs):
    monkeypatch.setattr(BaseNeuron, "should_set_weights", lambda self: True)
    validator, chain = _validator(tmp_path)
    validator.metagraph.last_update = None

    assert validator.should_set_weights() is False

    assert any(msg.startswith("Unable to read the age of this uid's weight record")
               for msg in _messages(logs, "warning"))
    assert chain.commit_reads == 0


def _fresh_config_module():
    """Execute config.py as a separate module, so the imported one is left untouched."""
    spec = importlib.util.spec_from_file_location("_herald_config_probe", cfg.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resubmit_interval_is_read_from_the_environment_and_defaults_to_180(monkeypatch):
    monkeypatch.delenv("HERALD_WEIGHT_RESUBMIT_BLOCKS", raising=False)
    assert _fresh_config_module().WEIGHT_RESUBMIT_BLOCKS == 180

    monkeypatch.setenv("HERALD_WEIGHT_RESUBMIT_BLOCKS", "360")
    assert _fresh_config_module().WEIGHT_RESUBMIT_BLOCKS == 360


def test_resubmit_interval_is_not_a_consensus_parameter(monkeypatch):
    fingerprint = consensus_fingerprint()
    monkeypatch.setattr(cfg, "WEIGHT_RESUBMIT_BLOCKS", 7)

    assert consensus_fingerprint() == fingerprint
    assert not any("resubmit" in key for key in consensus_params())


# --- bookkeeping ------------------------------------------------------------------------------------

def test_successful_submission_persists_scored_epoch(tmp_path, monkeypatch):
    validator, _chain = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    monkeypatch.setattr(BaseValidatorNeuron, "set_weights", lambda self: True)

    assert validator.set_weights() is True
    assert validator.herald_state.last_weight_epoch == 10
    assert HeraldState.load(str(tmp_path / "herald_state.json")).last_weight_epoch == 10


def test_failed_submission_does_not_advance_epoch(tmp_path, monkeypatch):
    validator, _chain = _validator(tmp_path, scored_epoch=10, weight_epoch=9)
    monkeypatch.setattr(BaseValidatorNeuron, "set_weights", lambda self: False)

    assert validator.set_weights() is False
    assert validator.herald_state.last_weight_epoch == 9


def test_last_weight_epoch_has_exactly_the_documented_writer():
    # state.py documents Validator.set_weights as the only writer (besides initialisation); keep
    # that documented statement true.
    writers = set()
    for top in ("herald", "neurons", "scripts", "core"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if re.search(r"\.last_weight_epoch\s*=(?!=)", line):
                            writers.add((os.path.relpath(path, ROOT), line.strip()))
    assert writers == {
        ("herald/validator/news/state.py", "self.last_weight_epoch = last_weight_epoch"),
        ("neurons/validator.py", "state.last_weight_epoch = state.last_scored_epoch"),
    }


def _patch_startup(tmp_path, monkeypatch):
    def base_init(self, config=None):
        self.config = SimpleNamespace(
            neuron=SimpleNamespace(full_path=str(tmp_path), disable_set_weights=True),
        )
        self.uid = 0

    monkeypatch.setattr(BaseValidatorNeuron, "__init__", base_init)
    monkeypatch.setattr("neurons.validator.get_cloudwatch_handler", lambda **kwargs: None)
    monkeypatch.delenv(ALLOW_FRESH_ON_CORRUPT_ENV, raising=False)


def test_validator_loads_herald_state_at_startup(tmp_path, monkeypatch):
    saved = HeraldState.fresh()
    saved.last_scored_epoch = 42
    saved.save(str(tmp_path / "herald_state.json"))
    _patch_startup(tmp_path, monkeypatch)

    assert Validator().herald_state.last_scored_epoch == 42


@pytest.mark.parametrize("wallet_hotkey, expected", [("hkOwner", True), ("hkOther", False)])
def test_startup_logs_whether_the_wallet_hotkey_holds_uid_zero(tmp_path, monkeypatch, wallet_hotkey,
                                                              expected):
    _patch_startup(tmp_path, monkeypatch)
    base_init = BaseValidatorNeuron.__init__

    def init_with_chain_view(self, config=None):
        base_init(self, config)
        self.wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address=wallet_hotkey))
        self.metagraph = SimpleNamespace(hotkeys=["hkOwner", "hkOther"])

    monkeypatch.setattr(BaseValidatorNeuron, "__init__", init_with_chain_view)
    logs = []
    monkeypatch.setattr("neurons.validator.bt.logging.info", lambda msg, *a, **k: logs.append(str(msg)))

    Validator()

    assert f"OWNER_VALIDATOR_CHECK wallet_hotkey_is_uid0={expected}" in logs


def test_validator_refuses_to_start_on_an_unreadable_state_file(tmp_path, monkeypatch):
    (tmp_path / "herald_state.json").write_text("{ not json")
    _patch_startup(tmp_path, monkeypatch)

    with pytest.raises(HeraldStateLoadError, match="Refusing to start"):
        Validator()
