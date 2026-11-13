"""herald.sdk.log: the level flags, the line format and the message helpers."""

import logging
import re
from types import SimpleNamespace

import pytest

from herald.sdk import log as logmod
from herald.sdk.log import log

LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \| (?P<level>.{8}) \| (?P<rest>.*)$")


@pytest.fixture(autouse=True)
def _restore_logging():
    state = log.current_state
    yield
    log._apply_file(False, None)
    {"Info": log.enable_info, "Debug": log.enable_debug, "Trace": log.enable_trace}.get(
        state, log.enable_default)()


def _cfg(**flags):
    base = dict(debug=False, trace=False, info=False, record_log=False, logging_dir=None,
                enable_third_party_loggers=False)
    base.update(flags)
    return SimpleNamespace(**base)


def _lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line]


def test_default_is_warning_and_the_flags_lower_it(capsys):
    log.enable_default()
    log.info("hidden")
    log.warning("shown")
    assert [LINE.match(line).group("rest") for line in _lines(capsys)] == ["shown"]

    log.set_config(_cfg(info=True))
    assert log.get_level() == logging.INFO
    assert logging.getLogger(logmod.SDK_LOGGER).level == logging.INFO
    log.set_config(_cfg())  # no flag: the level stays
    assert log.get_level() == logging.INFO
    log.set_config(_cfg(debug=True))
    assert log.get_level() == logging.DEBUG
    log.set_config(_cfg(trace=True, debug=True))
    assert log.get_level() == logmod.TRACE
    assert logging.getLogger(logmod.SDK_WIRE_LOGGER).level == logging.WARNING


def test_info_lines_have_the_plain_layout(capsys):
    log.enable_info()
    capsys.readouterr()
    log.info("EPOCH_WEIGHTS epoch=1")
    log.success("done")
    [first, second] = _lines(capsys)
    assert LINE.match(first).group("level") == "  INFO  "
    assert LINE.match(first).group("rest") == "EPOCH_WEIGHTS epoch=1"
    assert LINE.match(second).group("level") == "SUCCESS "


def test_debug_lines_name_the_logger_and_the_callers_line(capsys):
    log.enable_debug()
    capsys.readouterr()
    log.debug("detail")
    [line] = _lines(capsys)
    assert re.search(r"\| herald:test_log\.py:\d+ \| detail$", line), line


def test_prefix_and_suffix_join_as_before(capsys):
    log.enable_info()
    capsys.readouterr()
    log.error("set_weights failed", "Priority is too low")
    log.info("a", suffix="b")
    log.warning("[blue]netuid 69[/blue] :white_heavy_check_mark:")
    rest = [LINE.match(line).group("rest") for line in _lines(capsys)]
    assert rest == ["Priority is too low - set_weights failed", "a - b", "netuid 69 \u2705"]


def test_sdk_records_share_the_output(capsys):
    log.enable_info()
    capsys.readouterr()
    logging.getLogger("bittensor.transport").info("Falling back to endpoint wss://example.org")
    [line] = _lines(capsys)
    assert line.endswith("| Falling back to endpoint wss://example.org")


def test_record_log_writes_bittensor_log_in_the_logging_dir(tmp_path, capsys):
    log.set_config(_cfg(info=True, record_log=True, logging_dir=str(tmp_path)))
    log.info("to file")
    text = (tmp_path / "bittensor.log").read_text()
    assert re.search(r"\| herald:test_log\.py:\d+ \| to file", text)


def test_added_handlers_and_primary_loggers_receive_records(capsys):
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append((record.name, record.getMessage()))

    handler = Collect()
    log.enable_info()
    log.add_handler(handler)
    try:
        log.info("to cloudwatch")
        logging.getLogger("bittensor").warning("sdk warning")
    finally:
        for logger in log._primary:
            logger.removeHandler(handler)
        log._extra.remove(handler)
    assert ("herald", "to cloudwatch") in seen and ("bittensor", "sdk warning") in seen

    events = logging.getLogger("event-test")
    events.setLevel(38)
    log.register_primary_logger("event-test")
    capsys.readouterr()
    events.log(38, "an event")
    assert any(line.endswith("| an event") for line in _lines(capsys))
