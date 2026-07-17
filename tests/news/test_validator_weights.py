from types import SimpleNamespace

import numpy as np

from herald.base.validator import BaseValidatorNeuron


class ConcreteValidator(BaseValidatorNeuron):
    async def forward(self):
        return None

    def run(self):
        return None


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
