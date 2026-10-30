"""Structured logging and correlation IDs (IMP-0010).

Standard library only (plus sibling root modules). Offline: no network
handlers, no telemetry, no files unless the caller supplies a stream.

Design (PROPOSED engineering decisions; the Bible fixes only "structured
logging" in I1 and forbids remote telemetry):

* A correlation ID is 32 lower-case hex characters. Untrusted inbound values
  (e.g. an HTTP header) go through `coerce_correlation_id`, which never echoes
  an invalid value - it issues a fresh ID instead - so attacker-chosen text
  cannot reach the log stream (log injection / forgery).
* The ID travels in a `ContextVar` (`correlation_scope`), so it is correct
  across threads and asyncio tasks and is restored on exit, even on error.
* Every record is exactly one line of RFC 8785 canonical JSON (sorted keys,
  no raw newlines). Values are sanitised and size-capped; exceptions are
  reduced to type name and TRINETRA error code - tracebacks and exception
  messages are NOT emitted by default because they can carry sensitive data.
* The formatter never raises: a record that cannot be rendered degrades to a
  fixed minimal line. Field *names* are validated at the call site
  (`log_event`) because a bad name is a programmer error.
* Secret redaction helpers are a separate packet (IMP-0013); this module does
  not claim to detect secrets in values.
"""

from __future__ import annotations

import contextvars
import logging
import math
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import IO, Any, Final

from trinetra.errors import TrinetraError
from trinetra.jcs import MAX_SAFE_INTEGER, canonicalize
from trinetra.timeutil import Clock, format_utc

CORRELATION_ID_RE: Final = re.compile(r"[0-9a-f]{32}")
FIELD_NAME_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
EVENT_NAME_RE: Final = re.compile(r"[a-z][a-z0-9_.]{0,127}")
RESERVED_FIELDS: Final = frozenset({"ts", "level", "logger", "message", "event", "correlation_id",
                                    "exc_type", "exc_code"})
_SURROGATE_RE: Final = re.compile("[\ud800-\udfff]")
# Unicode line separators that JSON (and JCS) leave unescaped but that line-oriented
# log tooling may treat as line breaks. Escaped in output; the JSON value is unchanged.
_LINE_BREAKS: Final = {"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}
MAX_STRING_CHARS: Final = 2048
MAX_FIELDS: Final = 64
MAX_DEPTH: Final = 6
MAX_ITEMS: Final = 200
TRUNCATION_MARK: Final = "…[truncated]"
FIELDS_ATTR: Final = "trinetra_fields"
HANDLER_MARK: Final = "_trinetra_structured_handler"
LEVELS: Final = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
                 "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}

_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trinetra_correlation_id", default=None)


class LogError(TrinetraError):
    code = "LOG_INVALID"
    default_message = "invalid logging input"


# ------------------------------------------------------------ correlation ids
def new_correlation_id() -> str:
    return uuid.uuid4().hex


def validate_correlation_id(value: object) -> str:
    if not isinstance(value, str) or not CORRELATION_ID_RE.fullmatch(value):
        raise LogError("correlation id must be 32 lower-case hex characters",
                       details={"name": "correlation_id"})
    return value


def coerce_correlation_id(untrusted: object) -> str:
    """Return `untrusted` only if it is a well-formed ID; otherwise a fresh one.

    The invalid input is deliberately discarded, never logged or echoed.
    """
    if isinstance(untrusted, str) and CORRELATION_ID_RE.fullmatch(untrusted):
        return untrusted
    return new_correlation_id()


def current_correlation_id() -> str | None:
    return _correlation.get()


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the dynamic extent of the block (nestable)."""
    cid = new_correlation_id() if correlation_id is None else validate_correlation_id(correlation_id)
    token = _correlation.set(cid)
    try:
        yield cid
    finally:
        _correlation.reset(token)


# ------------------------------------------------------------------ sanitising
def _clip(text: str) -> str:
    text = _SURROGATE_RE.sub("\ufffd", text)  # lone surrogates cannot be encoded as UTF-8
    if len(text) > MAX_STRING_CHARS:
        return text[:MAX_STRING_CHARS] + TRUNCATION_MARK
    return text


def sanitize(value: Any, depth: int = 0) -> Any:
    """Reduce any object to a JCS-safe, bounded value. Never raises.

    Unknown objects become ``"<TypeName>"`` - never ``repr()``, which can leak
    internal state.
    """
    if value is None or isinstance(value, bool):
        return value
    kind = type(value)
    if kind is int:
        return value if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else _clip(str(value))
    if kind is float:
        return value if math.isfinite(value) else ("NaN" if math.isnan(value) else
                                                   ("Infinity" if value > 0 else "-Infinity"))
    if isinstance(value, str):
        return _clip(str.__str__(value))
    if depth >= MAX_DEPTH:
        return "<max-depth>"
    if isinstance(value, list | tuple):
        items = [sanitize(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(TRUNCATION_MARK)
        return items
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_ITEMS:
                out["_truncated"] = True
                break
            out[_clip(k) if isinstance(k, str) else f"<{type(k).__name__}>"] = sanitize(v, depth + 1)
        return out
    return f"<{kind.__name__}>"


# ------------------------------------------------------------------- formatter
class JsonFormatter(logging.Formatter):
    """One canonical-JSON line per record; never raises."""

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__()
        self._clock = clock

    def _timestamp(self, record: logging.LogRecord) -> str:
        moment = self._clock.now() if self._clock else datetime.fromtimestamp(record.created, UTC)
        return format_utc(moment, precision="microseconds")

    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._render(record)
        except Exception:  # noqa: BLE001 - a logger must never take the process down
            return _FALLBACK_LINE

    def _render(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - bad %-format arguments
            message = "<unformattable log message>"
        body: dict[str, Any] = {
            "ts": self._timestamp(record),
            "level": record.levelname if record.levelname in LEVELS else "ERROR",
            "logger": _clip(record.name),
            "message": _clip(message),
        }
        cid = current_correlation_id()
        if cid is not None:
            body["correlation_id"] = cid
        fields = getattr(record, FIELDS_ATTR, None)
        if isinstance(fields, Mapping):
            for key, value in fields.items():
                if isinstance(key, str) and FIELD_NAME_RE.fullmatch(key) and key not in RESERVED_FIELDS:
                    body[key] = sanitize(value)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            body["exc_type"] = _clip(type(exc).__name__)
            if isinstance(exc, TrinetraError):
                body["exc_code"] = exc.code
        line = canonicalize(body).decode("utf-8")
        for raw, escaped in _LINE_BREAKS.items():
            line = line.replace(raw, escaped)
        return line


_FALLBACK_LINE: Final = '{"level":"ERROR","message":"<log record could not be rendered>"}'


# ------------------------------------------------------------------- front ends
def log_event(logger: logging.Logger, level: str, event: str, /, **fields: Any) -> None:
    """Emit a structured event. Field *names* are validated (programmer errors)."""
    if level not in LEVELS:
        raise LogError(f"unknown log level {level!r}", details={"name": "level"})
    if not EVENT_NAME_RE.fullmatch(event):
        raise LogError("event name must be dotted/snake lower-case", details={"name": "event"})
    if len(fields) > MAX_FIELDS:
        raise LogError(f"more than {MAX_FIELDS} fields", details={"name": "fields"})
    for name in fields:
        if not FIELD_NAME_RE.fullmatch(name):
            raise LogError("field name must be lower_snake_case", details={"name": name})
        if name in RESERVED_FIELDS:
            raise LogError("field name is reserved", details={"name": name})
    logger.log(LEVELS[level], event, extra={FIELDS_ATTR: fields})


def configure_logging(
    *,
    level: str = "INFO",
    stream: IO[str] | None = None,
    logger_name: str = "trinetra",
    clock: Clock | None = None,
) -> logging.Handler:
    """Attach exactly one structured handler to `logger_name` (idempotent).

    Local stream only; the logger does not propagate to the root logger, so a
    host application cannot accidentally add a second, unstructured sink.
    """
    if level not in LEVELS:
        raise LogError(f"unknown log level {level!r}", details={"name": "level"})
    logger = logging.getLogger(logger_name)
    for existing in [h for h in logger.handlers if getattr(h, HANDLER_MARK, False)]:
        logger.removeHandler(existing)
        existing.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter(clock))
    setattr(handler, HANDLER_MARK, True)
    logger.addHandler(handler)
    logger.setLevel(LEVELS[level])
    logger.propagate = False
    return handler
