"""Herald's logging, on the standard library.

``log`` keeps the interface Herald's code has always used: ``log.info(msg, prefix="",
suffix="")`` and the same for ``debug``, ``warning``, ``error``, ``critical``, ``success`` and
``trace``; ``log.set_config(config.logging)`` applies the ``--logging.*`` flags; ``add_args`` adds
those flags. Without a flag the level is WARNING; ``--logging.info``, ``--logging.debug`` and
``--logging.trace`` lower it, and the BT_LOGGING_INFO / BT_LOGGING_DEBUG / BT_LOGGING_TRACE
environment variables set the level that applies before the flags are read.

Herald writes to the "herald" logger. The bittensor SDK writes to loggers under "bittensor" and
configures nothing itself; those records get the same handler and level, except the SDK's
websocket frame dump ("bittensor.transport.raw_websocket"), which stays at WARNING.

Lines go to stdout as ``2026-01-01 12:00:00.000 |   INFO   | message``; at DEBUG and TRACE the
logger name and the caller's file and line are added. ``--logging.record_log`` with
``--logging.logging_dir`` also writes them to ``<logging_dir>/bittensor.log``.
"""

import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

TRACE = 5
SUCCESS = 21
logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(SUCCESS, "SUCCESS")

HERALD_LOGGER = "herald"
SDK_LOGGER = "bittensor"
SDK_WIRE_LOGGER = "bittensor.transport.raw_websocket"

LOG_FILE_NAME = "bittensor.log"
MAX_LOG_FILE_BYTES = 25 * 1024 * 1024
LOG_BACKUP_COUNT = 10
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_BASE_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
_TRACE_FORMAT = "%(asctime)s | %(levelname)s | %(name)s:%(filename)s:%(lineno)s | %(message)s"
_PLAIN_LEVELS = {
    logging.NOTSET, TRACE, logging.DEBUG, logging.INFO, SUCCESS, logging.WARNING, logging.ERROR,
    logging.CRITICAL,
}
_EMOJI = {
    ":white_heavy_check_mark:": "✅",
    ":cross_mark:": "❌",
    ":satellite:": "\U0001f6f0️",
    ":warning:": "⚠️",
    ":arrow_right:": "➡️",
    ":hourglass:": "⏳",
}
# Colour markup some messages carry; the output is plain text, so it is removed.
_COLOUR_TAGS = tuple(
    tag for colour in ("red", "blue", "green", "magenta", "yellow", "orange")
    for tag in (f"[{colour}]", f"[/{colour}]")
)


def _env_flag(name: str) -> bool:
    # Any non-empty value turns the flag on, as bittensor's own defaults read these variables.
    return bool(os.getenv(name))


def default_logging_dir():
    """Where neuron state and log files live unless --logging.logging_dir says otherwise."""
    if os.getenv("READ_ONLY") == "1":
        return None
    return os.getenv("BT_LOGGING_LOGGING_DIR") or os.path.join(
        os.path.expanduser("~"), ".bittensor", "miners"
    )


def _concat(msg="", prefix="", suffix="") -> str:
    """``prefix - msg - suffix``, leaving out empty parts."""
    parts = [str(part).strip() for part in (prefix, msg, suffix)
             if part is not None and str(part).strip()]
    return " - ".join(parts)


def _clean(message: str) -> str:
    for text, emoji in _EMOJI.items():
        message = message.replace(text, emoji)
    for tag in _COLOUR_TAGS:
        message = message.replace(tag, "")
    return message


class LineFormatter(logging.Formatter):
    """``time.mmm | LEVEL | message``; with ``trace`` set, also ``name:file:line``."""

    def __init__(self, trace: bool = False):
        super().__init__(_BASE_FORMAT)
        self.trace = trace

    def formatTime(self, record, datefmt=None) -> str:
        return time.strftime(DATE_FORMAT, self.converter(record.created)) + f".{int(record.msecs):03d}"

    def format(self, record) -> str:
        record = logging.makeLogRecord(record.__dict__)
        record.levelname = f"{record.levelname:^8}"
        plain = record.levelno in _PLAIN_LEVELS and not self.trace
        self._style._fmt = _BASE_FORMAT if plain else _TRACE_FORMAT
        return super().format(record)


class _StdoutHandler(logging.StreamHandler):
    """A stream handler that writes to whatever ``sys.stdout`` is when a record is emitted."""

    def __init__(self):
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value):
        pass


class HeraldLogging:
    """Level control and the logging methods Herald calls; see the module docstring."""

    def __init__(self):
        self._logger = logging.getLogger(HERALD_LOGGER)
        self._formatter = LineFormatter()
        self._stream = _StdoutHandler()
        self._stream.setFormatter(self._formatter)
        self._file = None
        self._extra = []
        self._primary = []
        self._state = None
        self._third_party = False
        for name in (HERALD_LOGGER, SDK_LOGGER):
            self.register_primary_logger(name)
        logging.getLogger(SDK_WIRE_LOGGER).setLevel(logging.WARNING)
        self._apply_file(_env_flag("BT_LOGGING_RECORD_LOG"), default_logging_dir())
        if _env_flag("BT_LOGGING_TRACE"):
            self.enable_trace()
        elif _env_flag("BT_LOGGING_DEBUG"):
            self.enable_debug()
        elif _env_flag("BT_LOGGING_INFO"):
            self.enable_info()
        else:
            self.enable_default()

    # --- handlers --------------------------------------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        """The "herald" logger."""
        return self._logger

    def _handlers(self):
        return [self._stream] + ([self._file] if self._file else []) + list(self._extra)

    def register_primary_logger(self, name: str):
        """Send logger `name`'s records to this output too (its own level is left as it is)."""
        logger = logging.getLogger(name)
        if logger not in self._primary:
            self._primary.append(logger)
        logger.propagate = False
        for handler in self._handlers():
            if handler not in logger.handlers:
                logger.addHandler(handler)

    def route_to_root(self):
        """Hand every Herald and SDK record to the root logger's handlers instead of this output
        (for a process that configures logging itself, e.g. with ``logging.basicConfig``)."""
        for logger in self._primary:
            for handler in self._handlers():
                logger.removeHandler(handler)
            logger.propagate = True

    def add_handler(self, handler: logging.Handler):
        """Also send every Herald and SDK record to `handler` (e.g. a CloudWatch handler)."""
        self._extra.append(handler)
        for logger in self._primary:
            logger.addHandler(handler)

    def _apply_file(self, record_log, logging_dir):
        path = None
        if record_log and logging_dir:
            path = os.path.abspath(os.path.join(os.path.expanduser(logging_dir), LOG_FILE_NAME))
        if self._file is not None and getattr(self._file, "baseFilename", None) == path:
            return
        if self._file is not None:
            for logger in self._primary:
                logger.removeHandler(self._file)
            self._file.close()
            self._file = None
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=MAX_LOG_FILE_BYTES,
                                          backupCount=LOG_BACKUP_COUNT)
            handler.setFormatter(LineFormatter(trace=True))
            handler.setLevel(TRACE)
            self._file = handler
            for logger in self._primary:
                logger.addHandler(handler)

    def _enable_third_party(self):
        root = logging.getLogger()
        if self._stream not in root.handlers:
            root.addHandler(self._stream)
        self._third_party = True

    # --- levels ----------------------------------------------------------------------------------

    def _set_level(self, level: int, trace_format: bool):
        self._formatter.trace = trace_format
        for logger in self._primary:
            logger.setLevel(level)
        if self._third_party:
            logging.getLogger().setLevel(level)

    def enable_default(self):
        """WARNING and above."""
        self._logger.info("Enabling default logging (Warning level)")
        self._set_level(logging.WARNING, False)
        self._state = "Default"

    def enable_info(self):
        self._logger.info("Enabling info logging.")
        self._set_level(logging.INFO, False)
        self._state = "Info"
        self._logger.info("Info enabled.")

    def enable_debug(self):
        self._logger.info("Enabling debug.")
        self._set_level(logging.DEBUG, True)
        self._state = "Debug"
        self._logger.info("Debug enabled.")

    def enable_trace(self):
        self._logger.info("Enabling trace.")
        self._set_level(TRACE, True)
        self._state = "Trace"
        self._logger.info("Trace enabled.")

    def set_info(self, on: bool = True):
        if on and self._state != "Info":
            self.enable_info()
        elif not on and self._state == "Info":
            self.enable_default()

    def set_debug(self, on: bool = True):
        if on and self._state != "Debug":
            self.enable_debug()
        elif not on and self._state == "Debug":
            self.enable_default()

    def set_trace(self, on: bool = True):
        if on and self._state != "Trace":
            self.enable_trace()
        elif not on and self._state == "Trace":
            self.enable_default()

    def set_default(self):
        if self._state != "Default":
            self.enable_default()

    @property
    def current_state(self) -> str:
        """Default, Info, Debug or Trace."""
        return self._state

    def get_level(self) -> int:
        return self._logger.level

    def setLevel(self, level: int):
        for logger in self._primary:
            logger.setLevel(level)

    def set_config(self, config):
        """Apply a parsed ``config.logging`` namespace (or a config holding one).

        The level only changes when a flag asks for one, so a run without flags keeps the level
        the environment set.
        """
        cfg = getattr(config, "logging", None) or config
        self._apply_file(getattr(cfg, "record_log", False), getattr(cfg, "logging_dir", None))
        if getattr(cfg, "enable_third_party_loggers", False):
            self._enable_third_party()
        if getattr(cfg, "trace", False):
            self.enable_trace()
        elif getattr(cfg, "debug", False):
            self.enable_debug()
        elif getattr(cfg, "info", False):
            self.enable_info()

    @staticmethod
    def check_config(config):
        assert config.logging

    @staticmethod
    def add_args(parser: argparse.ArgumentParser, prefix: str = None):
        """The ``--logging.*`` flags, with the defaults bittensor 10 gave them."""
        add_args(parser, prefix)

    # --- messages --------------------------------------------------------------------------------

    def _emit(self, level, msg, prefix, suffix, args, kwargs, stacklevel):
        if not self._logger.isEnabledFor(level):
            return
        message = _clean(_concat(msg, prefix, suffix))
        self._logger.log(level, message, *args, stacklevel=stacklevel + 2, **kwargs)

    def trace(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(TRACE, msg, prefix, suffix, args, kwargs, stacklevel)

    def debug(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(logging.DEBUG, msg, prefix, suffix, args, kwargs, stacklevel)

    def info(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(logging.INFO, msg, prefix, suffix, args, kwargs, stacklevel)

    def success(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(SUCCESS, msg, prefix, suffix, args, kwargs, stacklevel)

    def warning(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(logging.WARNING, msg, prefix, suffix, args, kwargs, stacklevel)

    def error(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(logging.ERROR, msg, prefix, suffix, args, kwargs, stacklevel)

    def critical(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        self._emit(logging.CRITICAL, msg, prefix, suffix, args, kwargs, stacklevel)

    def exception(self, msg="", prefix="", suffix="", *args, stacklevel=1, **kwargs):
        kwargs.setdefault("exc_info", True)
        self._emit(logging.ERROR, msg, prefix, suffix, args, kwargs, stacklevel)


def add_args(parser: argparse.ArgumentParser, prefix: str = None):
    """Add ``--logging.debug``, ``.trace``, ``.info``, ``.record_log``, ``.logging_dir`` and
    ``.enable_third_party_loggers``. Adding them twice to one parser is a no-op."""
    p = "" if prefix is None else prefix + "."
    try:
        parser.add_argument(f"--{p}logging.debug", action="store_true",
                            help="Log at DEBUG.", default=_env_flag("BT_LOGGING_DEBUG"))
        parser.add_argument(f"--{p}logging.trace", action="store_true",
                            help="Log at TRACE.", default=_env_flag("BT_LOGGING_TRACE"))
        parser.add_argument(f"--{p}logging.info", action="store_true",
                            help="Log at INFO.", default=_env_flag("BT_LOGGING_INFO"))
        parser.add_argument(f"--{p}logging.record_log", action="store_true",
                            help="Also write the log to <logging_dir>/bittensor.log.",
                            default=_env_flag("BT_LOGGING_RECORD_LOG"))
        parser.add_argument(f"--{p}logging.logging_dir", type=str,
                            help="Root directory for neuron state and log files.",
                            default=default_logging_dir())
        parser.add_argument(f"--{p}logging.enable_third_party_loggers", action="store_true",
                            help="Also show other libraries' log records.",
                            default=os.getenv("BT_LOGGING_ENABLE_THIRD_PARTY_LOGGERS") or False)
    except argparse.ArgumentError:
        pass


log = HeraldLogging()
