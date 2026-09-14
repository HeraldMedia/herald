"""Decision logic of scripts/watchdog.py, driven by fake chain and backend readers (no network)."""

import importlib.util
import io
import os
import re
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "watchdog.py")
HOTKEY = "5ValidatorHotkeyPlaceholder"
HEAD = 7200 * 1255 + 10 + 400  # a block inside epoch 1255 for a 7200-block epoch and 10-block lag
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


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
    def __init__(self, decisions=(), fail=None, health=None, health_fail=None):
        self.decisions, self.fail, self.calls = list(decisions), fail, []
        self.health, self.health_fail, self.health_calls = health, health_fail, []

    def epochs(self, network, netuid):
        self.calls.append((network, netuid))
        if self.fail is not None:
            raise self.fail
        return self.decisions

    def feed_health(self, network, netuid):
        self.health_calls.append((network, netuid))
        if self.health_fail is not None:
            raise self.health_fail
        return self.health


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


def _health(**overrides):
    payload = {"network": "finney", "netuid": 69, "submitted_count": 2,
               "oldest_submitted_age_seconds": 5400, "last_feed_read_at": "2026-09-11T09:00:00+00:00",
               "last_feed_read_pending_rows": 2, "confirmed_epoch": 1254,
               "signer_last_seen_at": "2026-09-11T11:54:00Z", "alarms": []}
    payload.update(overrides)
    return payload


def test_board_feed_without_alarms_is_healthy(wd):
    finding = wd.check_board_feed(_health(), NOW, network="finney", netuid=69)
    assert finding.check == "board_feed" and finding.status == wd.OK
    assert "2 submitted placement(s), oldest 1.5 h" in finding.message
    assert "feed last read 3.0 h ago with 2 pending row(s)" in finding.message
    assert "confirmed epoch 1254" in finding.message and "signer last seen 0.1 h ago" in finding.message


def test_board_feed_with_nothing_submitted_and_no_read_yet_is_healthy(wd):
    payload = _health(submitted_count=0, oldest_submitted_age_seconds=None, last_feed_read_at=None,
                      last_feed_read_pending_rows=None, confirmed_epoch=None, signer_last_seen_at=None)
    finding = wd.check_board_feed(payload, NOW, network="finney", netuid=69)
    assert finding.status == wd.OK
    assert finding.message == "0 submitted placement(s); no feed read recorded"


DOCUMENTED_ALARMS = ("feed_not_read", "submitted_not_settled", "signer_stale", "pool_hotkey_unregistered",
                     "committed_not_submitted", "settlement_mismatch", "expired_claim_vesting")


def test_the_documented_alarm_codes_are_pinned(wd):
    assert wd.BOARD_FEED_ALARMS == DOCUMENTED_ALARMS


@pytest.mark.parametrize("code", DOCUMENTED_ALARMS)
def test_every_documented_alarm_is_a_breach(wd, code):
    assert code in wd.BOARD_FEED_ALARMS
    finding = wd.check_board_feed(_health(alarms=[{"code": code, "detail": "over its threshold"}]), NOW)
    assert finding.status == wd.BREACH
    assert f"feed health reports 1 alarm(s): {code}: over its threshold (2 submitted" in finding.message
    assert "unrecognised" not in finding.message
    assert wd.exit_code([finding]) == wd.EXIT_BREACH


def test_several_documented_alarms_are_each_reported_by_name(wd):
    codes = ("committed_not_submitted", "settlement_mismatch", "expired_claim_vesting")
    alarms = [{"code": code, "detail": f"{count} placement(s)"} for count, code in enumerate(codes, start=1)]
    finding = wd.check_board_feed(_health(alarms=alarms), NOW, network="finney", netuid=69)
    assert finding.status == wd.BREACH and wd.exit_code([finding]) == wd.EXIT_BREACH
    assert finding.message.startswith(
        "feed health reports 3 alarm(s): committed_not_submitted: 1 placement(s); "
        "settlement_mismatch: 2 placement(s); expired_claim_vesting: 3 placement(s) (2 submitted")
    assert "unrecognised" not in finding.message


def test_an_alarm_is_a_breach_even_with_nothing_submitted(wd):
    payload = _health(submitted_count=0, alarms=[{"code": "signer_stale", "detail": None}])
    finding = wd.check_board_feed(payload, NOW)
    assert finding.status == wd.BREACH and "alarm(s): signer_stale (0 submitted" in finding.message


def test_an_unrecognised_alarm_is_still_a_breach_and_its_detail_stays_one_short_line(wd):
    alarm = {"code": "new_alarm", "detail": "line one\nline two " + "x" * 500}
    finding = wd.check_board_feed(_health(alarms=[alarm]), NOW)
    assert finding.status == wd.BREACH
    assert "new_alarm (unrecognised): line one line two x" in finding.message
    assert "\n" not in finding.message and "x" * 200 not in finding.message


@pytest.mark.parametrize("payload", [
    None, [], "ok", {"alarms": []},
    _health(submitted_count="2"), _health(submitted_count=True), _health(submitted_count=-1),
    _health(alarms=None), _health(alarms={"code": "signer_stale"}),
    _health(alarms=[{"detail": "no code"}]), _health(alarms=["signer_stale"]),
    _health(alarms=[{"code": ""}, None, {"code": 3}]),
])
def test_malformed_feed_health_is_unknown_not_healthy(wd, payload):
    finding = wd.check_board_feed(payload, NOW, network="finney", netuid=69)
    assert finding.status == wd.UNKNOWN
    assert wd.exit_code([finding]) == wd.EXIT_UNKNOWN


def test_a_readable_alarm_outranks_alarm_entries_without_a_code(wd):
    alarms = ["junk", {"code": "feed_not_read", "detail": "no feed read in 36 h"}, {"detail": "no code"}]
    finding = wd.check_board_feed(_health(alarms=alarms), NOW, network="finney", netuid=69)
    assert finding.status == wd.BREACH and wd.exit_code([finding]) == wd.EXIT_BREACH
    assert finding.message.startswith("feed health reports 1 alarm(s): feed_not_read: no feed read in 36 h; "
                                      "also 2 alarm(s) without a code, the first 'junk' (2 submitted")


@pytest.mark.parametrize("overrides", [{"network": "test"}, {"network": None}, {"netuid": 70},
                                       {"netuid": True}, {"netuid": "69"}])
def test_feed_health_for_another_scope_is_unknown(wd, overrides):
    finding = wd.check_board_feed(_health(**overrides), NOW, network="finney", netuid=69)
    assert finding.status == wd.UNKNOWN and "feed health answered for" in finding.message


def test_unreadable_feed_times_are_reported_without_changing_the_status(wd):
    finding = wd.check_board_feed(_health(last_feed_read_at="yesterday", signer_last_seen_at=5), NOW)
    assert finding.status == wd.OK
    assert "unreadable last_feed_read_at 'yesterday'" in finding.message
    assert "signer last seen" not in finding.message


def test_board_feed_read_failure_is_unknown(wd):
    backend = FakeBackend(health_fail=ConnectionError("feed health down"))
    finding = wd.run_board_feed_check(backend, network="finney", netuid=69, now=NOW)
    assert finding.status == wd.UNKNOWN and "feed health down" in finding.message
    assert backend.health_calls == [("finney", 69)]


def test_main_runs_the_board_feed_check_only_when_asked(wd):
    backend = FakeBackend([_decision(1255)], health=_health(alarms=[{"code": "signer_stale"}]))
    code, text, _ = _main(wd, BASE_ARGS, FakeChain(), backend)
    assert code == wd.EXIT_OK and "board_feed" not in text
    assert backend.health_calls == []


def test_main_board_feed_alarm_is_a_breach(wd):
    alarm = {"code": "feed_not_read", "detail": "no feed read in 36 h"}
    backend = FakeBackend([_decision(1255)], health=_health(alarms=[alarm]))
    code, text, _ = _main(wd, BASE_ARGS + ["--check-board-feed"], FakeChain(), backend)
    assert code == wd.EXIT_BREACH and "watchdog: BREACH" in text
    assert "BREACH   board_feed: feed health reports 1 alarm(s): feed_not_read: no feed read in 36 h" in text
    assert "OK       last_update" in text and "OK       snapshot_epoch" in text
    assert backend.health_calls == [("finney", 69)]


def test_main_board_feed_healthy_exits_zero(wd):
    backend = FakeBackend([_decision(1255)], health=_health())
    code, text, _ = _main(wd, BASE_ARGS + ["--check-board-feed"], FakeChain(), backend)
    assert code == wd.EXIT_OK and "OK       board_feed: 2 submitted placement(s)" in text


def test_main_board_feed_still_runs_when_the_chain_is_unreachable(wd):
    backend = FakeBackend(health=_health(alarms=[{"code": "submitted_not_settled"}]))
    code, text, _ = _main(wd, BASE_ARGS + ["--check-board-feed"], ConnectionError("no route"), backend)
    assert code == wd.EXIT_BREACH
    assert "UNKNOWN  chain: cannot connect to finney" in text and "BREACH   board_feed" in text
    assert backend.calls == [] and backend.health_calls == [("finney", 69)]


def test_backend_reader_feed_health_is_one_get_without_credentials(wd, monkeypatch):
    import httpx

    seen = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return _health()

    def get(url, **kwargs):
        seen.append((url, kwargs))
        return Response()

    def refuse(*args, **kwargs):
        raise AssertionError("the watchdog sends GET requests only")

    monkeypatch.setenv("HERALD_RESULTS_TOKEN", "never-sent")
    monkeypatch.setenv("HERALD_RESULTS_READ_TOKEN", "never-sent")
    monkeypatch.setattr(httpx, "get", get)
    for verb in ("post", "put", "patch", "delete", "request", "stream"):
        monkeypatch.setattr(httpx, verb, refuse)

    assert wd.BackendReader("https://api.example/").feed_health("finney", 69) == _health()
    assert seen == [("https://api.example/public/placements/feed-health",
                     {"params": {"network": "finney", "netuid": 69}, "timeout": 15.0})]


def test_watchdog_sends_only_unauthenticated_gets():
    with open(SCRIPT, encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"\.(post|put|patch|delete|request|stream)\(", source)
    assert "headers" not in source and "TOKEN" not in source
