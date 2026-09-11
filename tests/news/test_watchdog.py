"""Decision logic of scripts/watchdog.py, driven by fake chain and backend readers (no network)."""

import importlib.util
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "watchdog.py")
HOTKEY = "5ValidatorHotkeyPlaceholder"
HEAD = 7200 * 1255 + 10 + 400  # a block inside epoch 1255 for a 7200-block epoch and 10-block lag


@pytest.fixture(scope="module")
def wd():
    name = "herald_watchdog_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


class FakeChain:
    def __init__(self, *, block=HEAD, uid=0, last_update=HEAD - 100, cutoff=5000, fail=None):
        self.block, self.uid, self.last_update_block = block, uid, last_update
        self.cutoff, self.fail, self.calls = cutoff, fail, []

    def _call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise ConnectionError(f"{name} unavailable")

    def current_block(self):
        self._call("current_block")
        return self.block

    def uid_for_hotkey(self, hotkey, netuid):
        self._call("uid_for_hotkey")
        return self.uid

    def last_update(self, netuid, uid):
        self._call("last_update")
        return self.last_update_block

    def activity_cutoff(self, netuid):
        self._call("activity_cutoff")
        return self.cutoff


class FakeBackend:
    def __init__(self, decisions=(), fail=None):
        self.decisions, self.fail, self.calls = list(decisions), fail, []

    def epochs(self, network, netuid):
        self.calls.append((network, netuid))
        if self.fail is not None:
            raise self.fail
        return self.decisions


def _decision(epoch, status="confirmed"):
    return {"network": "finney", "netuid": 69, "epoch": epoch, "status": status,
            "snapshot": {"validator_hotkey": HOTKEY}}


def _run(wd, chain, backend, **overrides):
    kwargs = dict(hotkey=HOTKEY, netuid=69, network="finney", max_update_age_blocks=None,
                  max_epoch_lag=1, epoch_len=7200, epoch_lag=10)
    kwargs.update(overrides)
    findings = wd.run_checks(chain, backend, **kwargs)
    return findings, {f.check: f for f in findings}


def test_herald_epoch_matches_the_forward_pass(wd):
    assert wd.herald_epoch(HEAD, 7200, 10) == 1255
    assert wd.herald_epoch(7200 * 1256 + 9, 7200, 10) == 1255
    assert wd.herald_epoch(7200 * 1256 + 10, 7200, 10) == 1256
    assert wd.herald_epoch(5, 7200, 10) == 0
    with open(os.path.join(ROOT, "herald", "validator", "news", "forward.py"), encoding="utf-8") as f:
        assert "epoch = max(0, block - HERALD_EPOCH_LAG) // VEST_EPOCH_LEN" in f.read()


def test_healthy_validator_passes_both_checks(wd):
    findings, by_check = _run(wd, FakeChain(), FakeBackend([_decision(1254), _decision(1255)]))
    assert {f.status for f in findings} == {wd.OK}
    assert wd.exit_code(findings) == wd.EXIT_OK
    assert "epoch 1255" in by_check["snapshot_epoch"].message


def test_stale_last_update_is_a_breach_against_activity_cutoff_by_default(wd):
    chain = FakeChain(last_update=HEAD - 5001, cutoff=5000)
    findings, by_check = _run(wd, chain, FakeBackend([_decision(1255)]))
    assert by_check["last_update"].status == wd.BREACH
    assert "5001 blocks" in by_check["last_update"].message
    assert "threshold 5000" in by_check["last_update"].message
    assert wd.exit_code(findings) == wd.EXIT_BREACH

    _, at_cutoff = _run(wd, FakeChain(last_update=HEAD - 5000, cutoff=5000), FakeBackend([_decision(1255)]))
    assert at_cutoff["last_update"].status == wd.OK


def test_explicit_age_threshold_overrides_activity_cutoff(wd):
    chain = FakeChain(last_update=HEAD - 400, cutoff=5000)
    _, by_check = _run(wd, chain, FakeBackend([_decision(1255)]), max_update_age_blocks=360)
    assert by_check["last_update"].status == wd.BREACH
    assert "activity_cutoff" not in chain.calls


def test_unregistered_hotkey_is_a_breach(wd):
    _, by_check = _run(wd, FakeChain(uid=None), FakeBackend([_decision(1255)]))
    assert by_check["last_update"].status == wd.BREACH
    assert "not registered" in by_check["last_update"].message


@pytest.mark.parametrize("latest,status", [(1255, "ok"), (1254, "ok"), (1253, "breach")])
def test_snapshot_lag_threshold(wd, latest, status):
    _, by_check = _run(wd, FakeChain(), FakeBackend([_decision(latest, "provisional")]))
    assert by_check["snapshot_epoch"].status == status


def test_backend_with_no_snapshots_is_a_breach(wd):
    _, by_check = _run(wd, FakeChain(), FakeBackend([]))
    assert by_check["snapshot_epoch"].status == wd.BREACH
    assert "no validator snapshot" in by_check["snapshot_epoch"].message


def test_backend_ahead_of_chain_is_a_breach(wd):
    _, by_check = _run(wd, FakeChain(), FakeBackend([_decision(1257)]))
    assert by_check["snapshot_epoch"].status == wd.BREACH
    assert "ahead of the chain" in by_check["snapshot_epoch"].message


def test_unreadable_backend_payload_is_unknown_not_healthy(wd):
    findings, by_check = _run(wd, FakeChain(), FakeBackend([{"epoch": "soon"}, "junk"]))
    assert by_check["snapshot_epoch"].status == wd.UNKNOWN
    assert wd.exit_code(findings) == wd.EXIT_UNKNOWN


def test_unreachable_chain_is_unknown_not_healthy(wd):
    backend = FakeBackend([_decision(1255)])
    findings, by_check = _run(wd, FakeChain(fail="current_block"), backend)
    assert list(by_check) == ["chain"] and by_check["chain"].status == wd.UNKNOWN
    assert wd.exit_code(findings) == wd.EXIT_UNKNOWN


def test_unreachable_backend_is_unknown_and_a_breach_still_wins(wd):
    error = ConnectionError("backend down")
    findings, by_check = _run(wd, FakeChain(), FakeBackend(fail=error))
    assert by_check["snapshot_epoch"].status == wd.UNKNOWN
    assert "backend down" in by_check["snapshot_epoch"].message
    assert wd.exit_code(findings) == wd.EXIT_UNKNOWN

    findings, _ = _run(wd, FakeChain(last_update=HEAD - 9000), FakeBackend(fail=error))
    assert wd.exit_code(findings) == wd.EXIT_BREACH


def test_last_update_read_failure_is_unknown(wd):
    _, by_check = _run(wd, FakeChain(fail="last_update"), FakeBackend([_decision(1255)]))
    assert by_check["last_update"].status == wd.UNKNOWN
    assert by_check["snapshot_epoch"].status == wd.OK


def _main(wd, argv, chain, backend):
    out, targets = io.StringIO(), []

    def chain_factory(target):
        targets.append(target)
        if isinstance(chain, Exception):
            raise chain
        return chain

    code = wd.main(argv, chain_factory=chain_factory, backend_factory=lambda url: backend, out=out)
    return code, out.getvalue(), targets


BASE_ARGS = ["--hotkey", HOTKEY, "--netuid", "69", "--network", "finney",
             "--backend-url", "https://api.example", "--epoch-len", "7200", "--epoch-lag", "10"]


def test_main_prints_every_finding_and_exits_non_zero_on_breach(wd):
    backend = FakeBackend([_decision(1255)])
    code, text, targets = _main(wd, BASE_ARGS, FakeChain(last_update=HEAD - 6000), backend)
    assert code == wd.EXIT_BREACH
    assert "BREACH   last_update: weights are stale" in text and "6000 blocks" in text
    assert "OK       snapshot_epoch" in text and "watchdog: BREACH" in text
    assert targets == ["finney"] and backend.calls == [("finney", 69)]


def test_main_healthy_exit_zero_and_chain_endpoint_override(wd):
    code, text, targets = _main(wd, BASE_ARGS + ["--chain-endpoint", "wss://node.example:443"],
                                FakeChain(), FakeBackend([_decision(1255)]))
    assert code == wd.EXIT_OK and "watchdog: healthy" in text
    assert targets == ["wss://node.example:443"]


def test_main_connection_failure_is_incomplete(wd):
    code, text, _ = _main(wd, BASE_ARGS, ConnectionError("no route"), FakeBackend([_decision(1255)]))
    assert code == wd.EXIT_UNKNOWN and "no route" in text and "INCOMPLETE" in text


def test_main_unexpected_error_is_incomplete_not_a_breach(wd):
    out = io.StringIO()

    def broken_backend(url):
        raise RuntimeError("bad backend url")

    code = wd.main(BASE_ARGS, chain_factory=lambda target: FakeChain(),
                   backend_factory=broken_backend, out=out)

    assert code == wd.EXIT_UNKNOWN
    assert "bad backend url" in out.getvalue() and "INCOMPLETE" in out.getvalue()


def test_epoch_defaults_mirror_herald_config_without_importing_herald(wd, monkeypatch):
    monkeypatch.delenv("HERALD_VEST_EPOCH_LEN", raising=False)
    monkeypatch.delenv("HERALD_EPOCH_LAG", raising=False)
    assert wd._herald_epoch_defaults() == (7200, 10)
    with open(os.path.join(ROOT, "herald", "validator", "utils", "config.py"), encoding="utf-8") as f:
        config_source = f.read()
    assert "VEST_EPOCH_LEN = int(os.getenv('HERALD_VEST_EPOCH_LEN', '7200'))" in config_source
    assert "HERALD_EPOCH_LAG = int(os.getenv('HERALD_EPOCH_LAG', '10'))" in config_source

    monkeypatch.setenv("HERALD_VEST_EPOCH_LEN", "360")
    monkeypatch.setenv("HERALD_EPOCH_LAG", "3")
    assert wd._herald_epoch_defaults() == (360, 3)


def test_invalid_epoch_env_is_incomplete_not_a_crash(wd, monkeypatch):
    monkeypatch.setenv("HERALD_VEST_EPOCH_LEN", "daily")
    out = io.StringIO()
    code = wd.main(["--hotkey", HOTKEY, "--network", "finney"],
                   chain_factory=lambda target: FakeChain(),
                   backend_factory=lambda url: FakeBackend([_decision(1255)]), out=out)
    assert code == wd.EXIT_UNKNOWN and "daily" in out.getvalue()


def test_watchdog_never_reads_the_state_file_weight_epoch_nor_imports_herald():
    with open(SCRIPT, encoding="utf-8") as f:
        source = f.read()
    assert "last_weight_epoch" not in source
    assert "HeraldState" not in source
    assert "import herald" not in source and "from herald" not in source
