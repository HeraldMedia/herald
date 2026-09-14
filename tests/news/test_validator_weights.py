import logging
from types import SimpleNamespace

import numpy as np
import pytest

from herald.base import validator as base_validator
from herald.base.validator import BaseValidatorNeuron

UNKNOWN_BLOCK = "UnknownBlock: Header was not found in the database"
ENDPOINT = "wss://entrypoint-finney.opentensor.ai:443"
FALLBACK = "wss://fallback.example:443"


class ConcreteValidator(BaseValidatorNeuron):
    async def forward(self):
        return None

    def run(self):
        return None


@pytest.fixture(autouse=True)
def _weight_check_env(monkeypatch):
    for name in (base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, base_validator.WEIGHT_CHECK_BACKOFF_ENV,
                 base_validator.WEIGHT_CHECK_FALLBACK_ENV,
                 base_validator.WEIGHT_SUPPRESSION_ALERT_ENV):
        monkeypatch.delenv(name, raising=False)


def _validator(monkeypatch, *, pending=False):
    validator = object.__new__(ConcreteValidator)
    monkeypatch.setattr(ConcreteValidator, "block", property(lambda self: 200))
    validator.step = 1
    validator.uid = 1
    validator.config = SimpleNamespace(
        netuid=1,
        neuron=SimpleNamespace(disable_set_weights=False, epoch_length=10),
    )
    validator.metagraph = SimpleNamespace(
        n=2,
        uids=np.array([0, 1]),
        last_update=np.array([0, 100]),
    )
    validator.wallet = SimpleNamespace(
        hotkey=SimpleNamespace(ss58_address="validator-hotkey")
    )
    validator.subtensor = SimpleNamespace(
        commit_reveal_enabled=lambda netuid: True,
        get_timelocked_weight_commits=lambda netuid: (
            [("validator-hotkey", 190, "encrypted", 123)] if pending else []
        ),
    )
    return validator


def test_pending_timelocked_commit_suppresses_weight_resubmission(monkeypatch):
    validator = _validator(monkeypatch, pending=True)

    assert validator.should_set_weights() is False


def test_no_pending_timelocked_commit_allows_weight_submission(monkeypatch):
    validator = _validator(monkeypatch, pending=False)

    assert validator.should_set_weights() is True


def test_crv4_submission_waits_for_inclusion_and_reports_pending_reveal(monkeypatch):
    validator = _validator(monkeypatch)
    validator.scores = np.array([0.5, 0.1], dtype=np.float32)
    calls = []
    messages = []

    def submit(**kwargs):
        calls.append(kwargs)
        return True, "included"

    validator.subtensor.set_weights = submit
    monkeypatch.setattr(
        "herald.base.validator.process_weights_for_netuid",
        lambda **kwargs: (np.array([0, 1]), np.array([0.833333, 0.166667])),
    )
    monkeypatch.setattr(
        "herald.base.validator.convert_weights_and_uids_for_emit",
        lambda **kwargs: ([0, 1], [65535, 13107]),
    )
    monkeypatch.setattr("herald.base.validator.bt.logging.info", messages.append)

    assert validator.set_weights() is True

    assert calls[0]["wait_for_inclusion"] is True
    assert calls[0]["wait_for_finalization"] is False
    assert calls[0]["wait_for_revealed_execution"] is False
    assert any("pending automatic reveal" in message for message in messages)
    assert all("on chain successfully" not in message for message in messages)


def test_zero_scores_skip_weight_submission(monkeypatch):
    validator = _validator(monkeypatch)
    validator.scores = np.zeros(2, dtype=np.float32)
    calls = []
    messages = []
    validator.subtensor.set_weights = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setattr("herald.base.validator.bt.logging.info", messages.append)

    assert validator.set_weights() is False

    assert calls == []
    assert any("no rewarded miners" in message.lower() for message in messages)


# --- pending-commit check failures ----------------------------------------------------------------

def _flaky_validator(monkeypatch, *, failures, pending=False):
    """A validator whose commit query raises ``failures`` times (-1 = always), then answers."""
    validator = _validator(monkeypatch, pending=pending)
    calls = {"commits": 0}

    def commits(netuid):
        calls["commits"] += 1
        if failures < 0 or calls["commits"] <= failures:
            raise RuntimeError(UNKNOWN_BLOCK)
        return [("validator-hotkey", 190, "encrypted", 123)] if pending else []

    validator.subtensor.get_timelocked_weight_commits = commits
    validator.subtensor.chain_endpoint = ENDPOINT
    sleeps = []
    monkeypatch.setattr(base_validator.time, "sleep", sleeps.append)
    logs = {"error": [], "warning": [], "info": []}
    for level, sink in logs.items():
        monkeypatch.setattr(base_validator.bt.logging, level,
                            lambda msg="", *args, _sink=sink, **kwargs: _sink.append(str(msg)))
    return validator, calls, sleeps, logs


def _alerts(logs):
    return [m for m in logs["error"] if base_validator.WEIGHT_SUPPRESSION_ALERT_TAG in m]


def test_transient_check_failure_is_retried_at_chain_head_then_allows_submission(monkeypatch):
    validator, calls, sleeps, logs = _flaky_validator(monkeypatch, failures=1)

    assert validator.should_set_weights() is True

    assert calls["commits"] == 2
    assert sleeps == [4.0]
    assert logs["error"] == []
    assert any(UNKNOWN_BLOCK in m and ENDPOINT in m for m in logs["warning"])


def test_retry_that_finds_a_pending_commit_still_blocks_submission(monkeypatch):
    validator, calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=2, pending=True)

    assert validator.should_set_weights() is False

    assert calls["commits"] == 3
    assert logs["error"] == []
    assert any("pending automatic reveal" in m for m in logs["info"])


def test_persistent_check_failure_suppresses_with_an_error_naming_endpoint_and_cause(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_BACKOFF_ENV, "1.5")
    validator, calls, sleeps, logs = _flaky_validator(monkeypatch, failures=-1)

    assert validator.should_set_weights() is False

    assert calls["commits"] == 3
    assert sleeps == [1.5, 3.0]
    assert len(logs["error"]) == 1
    assert ENDPOINT in logs["error"][0] and UNKNOWN_BLOCK in logs["error"][0]
    assert "Weight submission suppressed" in logs["error"][0]
    assert _alerts(logs) == []
    assert validator._suppressed_weight_submissions == 1


def test_failed_check_never_reaches_set_weights_through_sync(monkeypatch):
    validator, _calls, _sleeps, _logs = _flaky_validator(monkeypatch, failures=-1)
    submitted = []
    validator.set_weights = lambda: submitted.append(True)
    validator.check_registered = lambda: None
    validator.should_sync_metagraph = lambda: False
    validator.save_state = lambda: None

    validator.sync()

    assert submitted == []


def test_consecutive_suppressions_cross_the_threshold_with_a_distinct_error(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_SUPPRESSION_ALERT_ENV, "2")
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "1")
    validator, _calls, sleeps, logs = _flaky_validator(monkeypatch, failures=-1)

    validator.should_set_weights()
    assert _alerts(logs) == []

    validator.should_set_weights()
    assert len(_alerts(logs)) == 1
    assert "2 consecutive weight submissions suppressed" in _alerts(logs)[0]
    assert ENDPOINT in _alerts(logs)[0]

    validator.should_set_weights()
    assert len(_alerts(logs)) == 2
    assert validator._suppressed_weight_submissions == 3
    assert sleeps == []


def test_a_successful_check_resets_the_suppression_count(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "1")
    validator, _calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=2)

    assert validator.should_set_weights() is False
    assert validator.should_set_weights() is False
    assert validator.should_set_weights() is True

    assert validator._suppressed_weight_submissions == 0
    assert any("recovered after 2" in m for m in logs["warning"])


def test_fallback_endpoint_answers_when_the_primary_keeps_failing(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "2")
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_FALLBACK_ENV, FALLBACK)
    validator, calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=-1)
    created = []
    fallback = SimpleNamespace(
        commit_reveal_enabled=lambda netuid: True,
        get_timelocked_weight_commits=lambda netuid: [("validator-hotkey", 190, "enc", 1)],
    )

    def make_subtensor(network):
        created.append(network)
        return fallback

    monkeypatch.setattr(base_validator.bt, "Subtensor", make_subtensor)

    assert validator.should_set_weights() is False  # pending on the fallback: still blocked
    assert calls["commits"] == 2 and created == [FALLBACK]
    assert logs["error"] == []

    fallback.get_timelocked_weight_commits = lambda netuid: []
    assert validator.should_set_weights() is True
    assert created == [FALLBACK]  # connection reused
    assert any(FALLBACK in m for m in logs["warning"])


def test_fallback_failure_still_suppresses_and_names_both_endpoints(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_FALLBACK_ENV, FALLBACK)
    validator, _calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=-1)

    def unreachable(network):
        raise ConnectionError("fallback down")

    monkeypatch.setattr(base_validator.bt, "Subtensor", unreachable)

    assert validator.should_set_weights() is False
    assert len(logs["error"]) == 1
    assert ENDPOINT in logs["error"][0] and FALLBACK in logs["error"][0]
    assert "fallback down" in logs["error"][0] and UNKNOWN_BLOCK in logs["error"][0]


def test_commit_reveal_disabled_does_not_query_commits(monkeypatch):
    validator = _validator(monkeypatch)
    validator.subtensor.commit_reveal_enabled = lambda netuid: False
    validator.subtensor.get_timelocked_weight_commits = (
        lambda netuid: pytest.fail("commits must not be queried when commit-reveal is off")
    )

    assert validator.should_set_weights() is True


def test_nothing_to_submit_skips_the_check_its_sleeps_and_its_alert(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_SUPPRESSION_ALERT_ENV, "1")
    validator, calls, sleeps, logs = _flaky_validator(monkeypatch, failures=-1)
    validator._has_weights_to_submit = lambda: False

    for _ in range(3):
        assert validator.should_set_weights() is False

    assert calls["commits"] == 0 and sleeps == []
    assert logs["error"] == [] and _alerts(logs) == []
    assert getattr(validator, "_suppressed_weight_submissions", 0) == 0


def test_endpoint_credentials_never_reach_the_logs(monkeypatch):
    primary = "wss://finney-rpc.example/ws/PRIMARYKEY?token=PRIMARYQ"
    fallback = "wss://user:hunter2@rpc.example:443/v1/FALLBACKKEY?apikey=FALLBACKQ"
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "2")
    monkeypatch.setenv(base_validator.WEIGHT_SUPPRESSION_ALERT_ENV, "1")
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_FALLBACK_ENV, fallback)
    validator, _calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=-1)
    validator.subtensor.chain_endpoint = primary

    def commits(netuid):
        raise ConnectionError(f"cannot reach {primary}")

    def unreachable(network):
        raise ConnectionError(f"handshake with {network} failed")

    validator.subtensor.get_timelocked_weight_commits = commits
    monkeypatch.setattr(base_validator.bt, "Subtensor", unreachable)

    assert validator.should_set_weights() is False

    text = "\n".join(logs["error"] + logs["warning"])
    assert "wss://finney-rpc.example" in text and "wss://rpc.example:443" in text
    assert _alerts(logs)
    for secret in ("PRIMARYKEY", "PRIMARYQ", "FALLBACKKEY", "FALLBACKQ", "hunter2"):
        assert secret not in text


def test_scrubbing_survives_prefix_endpoints_and_bare_secret_parts():
    primary = "wss://rpc.example.com/"
    fallback = "wss://user:pw-SECRET@rpc.example.com/v1/KEY9?apikey=QKEY77"
    error = RuntimeError(f"Could not connect to {fallback}; GET /v1/KEY9?apikey=QKEY77 HTTP 401; "
                         f"retry {primary} refused")

    text = base_validator._scrubbed(error, primary, fallback)

    for secret in ("KEY9", "QKEY77", "pw-SECRET"):
        assert secret not in text
    assert "Could not connect to wss://rpc.example.com;" in text
    assert base_validator._scrubbed(RuntimeError("finney node down"), "finney") == "finney node down"


def test_a_closed_submission_window_resets_the_suppression_count(monkeypatch):
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(base_validator.WEIGHT_SUPPRESSION_ALERT_ENV, "3")
    validator, _calls, _sleeps, logs = _flaky_validator(monkeypatch, failures=-1)
    for _ in range(2):
        assert validator.should_set_weights() is False
    assert validator._suppressed_weight_submissions == 2

    validator._has_weights_to_submit = lambda: False
    assert validator.should_set_weights() is False
    del validator._has_weights_to_submit

    assert validator.should_set_weights() is False
    assert validator._suppressed_weight_submissions == 1 and _alerts(logs) == []


def test_fallback_connection_is_built_with_bittensor_debug_muted(monkeypatch):
    # bittensor's Subtensor logs its chain endpoint verbatim at DEBUG while it connects.
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_ATTEMPTS_ENV, "1")
    monkeypatch.setenv(base_validator.WEIGHT_CHECK_FALLBACK_ENV, FALLBACK)
    validator, _calls, _sleeps, _logs = _flaky_validator(monkeypatch, failures=-1)
    level = {"now": logging.DEBUG, "while_connecting": None}
    monkeypatch.setattr(base_validator.bt.logging, "get_level", lambda: level["now"])
    monkeypatch.setattr(base_validator.bt.logging, "setLevel", lambda value: level.update(now=value))

    def make_subtensor(network):
        level["while_connecting"] = level["now"]
        return SimpleNamespace(commit_reveal_enabled=lambda netuid: True,
                               get_timelocked_weight_commits=lambda netuid: [])

    monkeypatch.setattr(base_validator.bt, "Subtensor", make_subtensor)

    assert validator.should_set_weights() is True
    assert level["while_connecting"] >= logging.INFO
    assert level["now"] == logging.DEBUG
