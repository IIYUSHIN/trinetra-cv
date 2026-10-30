"""IMP-0010 tests: structured logging and correlation IDs."""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import threading
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.digest import DigestError
from trinetra.errors import TrinetraError
from trinetra.structured_log import (
    FIELDS_ATTR,
    MAX_DEPTH,
    MAX_FIELDS,
    MAX_ITEMS,
    MAX_STRING_CHARS,
    TRUNCATION_MARK,
    JsonFormatter,
    LogError,
    coerce_correlation_id,
    configure_logging,
    correlation_scope,
    current_correlation_id,
    log_event,
    new_correlation_id,
    sanitize,
    validate_correlation_id,
)
from trinetra.timeutil import FixedClock

CID = "0123456789abcdef0123456789abcdef"
FIXED = FixedClock(datetime(2026, 9, 2, 14, 35, 20, 123456, tzinfo=UTC))


@pytest.fixture
def sink():
    """A private logger writing structured lines to a StringIO."""
    name = f"trinetra.test.{id(object())}"
    stream = io.StringIO()
    handler = configure_logging(level="DEBUG", stream=stream, logger_name=name, clock=FIXED)
    logger = logging.getLogger(name)
    yield logger, stream
    logger.removeHandler(handler)
    handler.close()


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().split("\n") if line]


# ---------------------------------------------------------------- happy path
def test_one_canonical_json_line_with_expected_fields(sink):
    logger, stream = sink
    with correlation_scope(CID):
        log_event(logger, "INFO", "asset.admitted", asset_count=3, ok=True)
    raw = stream.getvalue()
    assert raw.count("\n") == 1 and raw.endswith("\n")
    assert raw.rstrip("\n") == (
        '{"asset_count":3,"correlation_id":"' + CID + '","level":"INFO","logger":"' + logger.name
        + '","message":"asset.admitted","ok":true,"ts":"2026-09-02T14:35:20.123456Z"}'
    )


def test_plain_stdlib_calls_are_structured_too(sink):
    logger, stream = sink
    logger.warning("disk at %d%%", 91)
    [record] = lines(stream)
    assert record["message"] == "disk at 91%" and record["level"] == "WARNING"
    assert "correlation_id" not in record


def test_keys_are_sorted_and_timestamp_uses_injected_clock(sink):
    logger, stream = sink
    log_event(logger, "INFO", "e", zeta=1, alpha=2)
    keys = list(lines(stream)[0])
    assert keys == sorted(keys) and lines(stream)[0]["ts"].endswith(".123456Z")


def test_real_timestamp_is_utc_microsecond_format():
    stream = io.StringIO()
    handler = configure_logging(stream=stream, logger_name="trinetra.test.realclock")
    logging.getLogger("trinetra.test.realclock").info("hi")
    handler.close()
    assert lines(stream)[0]["ts"][-8:-1].startswith(".") and lines(stream)[0]["ts"].endswith("Z")


def test_new_ids_are_valid_and_unique():
    ids = {new_correlation_id() for _ in range(1000)}
    assert len(ids) == 1000 and all(validate_correlation_id(i) == i for i in ids)


def test_nested_scopes_restore_on_exit_and_on_error():
    assert current_correlation_id() is None
    with correlation_scope(CID):
        with correlation_scope() as inner:
            assert current_correlation_id() == inner != CID
        assert current_correlation_id() == CID
        with pytest.raises(RuntimeError), correlation_scope():
            raise RuntimeError("boom")
        assert current_correlation_id() == CID
    assert current_correlation_id() is None


def test_exception_logs_type_and_code_but_not_message_or_traceback(sink):
    logger, stream = sink
    try:
        raise DigestError("password=hunter2")
    except DigestError:
        logger.exception("failed")
    [record] = lines(stream)
    assert record["exc_type"] == "DigestError" and record["exc_code"] == "DIGEST_INVALID"
    assert "hunter2" not in stream.getvalue() and "Traceback" not in stream.getvalue()


def test_non_trinetra_exception_has_no_code(sink):
    logger, stream = sink
    try:
        raise ValueError("secret")
    except ValueError:
        logger.exception("x")
    [record] = lines(stream)
    assert record["exc_type"] == "ValueError" and "exc_code" not in record
    assert "secret" not in stream.getvalue()


def test_configure_is_idempotent_single_handler_no_propagation():
    name = "trinetra.test.idem"
    for _ in range(3):
        configure_logging(stream=io.StringIO(), logger_name=name)
    logger = logging.getLogger(name)
    assert len(logger.handlers) == 1 and logger.propagate is False
    logger.handlers[0].close()
    logger.handlers.clear()


# ------------------------------------------------------------ context isolation
def test_threads_do_not_see_each_others_ids():
    seen, barrier = {}, threading.Barrier(2)

    def work(tag):
        with correlation_scope(tag * 32):
            barrier.wait()
            seen[tag] = current_correlation_id()

    threads = [threading.Thread(target=work, args=(c,)) for c in "ab"]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {"a": "a" * 32, "b": "b" * 32} and current_correlation_id() is None


def test_asyncio_tasks_are_isolated():
    async def job(tag):
        with correlation_scope(tag * 32):
            await asyncio.sleep(0)
            return current_correlation_id()

    async def main():
        return await asyncio.gather(job("a"), job("b"), job("c"))

    assert asyncio.run(main()) == ["a" * 32, "b" * 32, "c" * 32]


def test_copy_context_carries_the_id_into_executors():
    with correlation_scope(CID):
        ctx = contextvars.copy_context()
    assert ctx.run(current_correlation_id) == CID and current_correlation_id() is None


# ---------------------------------------------------- hostile / injection
@pytest.mark.parametrize(
    "evil",
    ['x"\n{"level":"INFO","message":"forged"}', "a\r\nb", "\x00\x1b[31m", "  ", '"}\n{"x":"'],
)
def test_newlines_and_quotes_cannot_forge_a_second_log_line(sink, evil):
    logger, stream = sink
    log_event(logger, "INFO", "e", user_input=evil)
    logger.info(evil)
    raw = stream.getvalue()
    assert raw.count("\n") == 2 and not any(c in raw for c in "\r\u0085\u2028\u2029\x0b\x0c\x1c\x1d\x1e")
    for record in lines(stream):
        assert "forged" not in record.get("level", "") and record["level"] == "INFO"


@pytest.mark.parametrize("evil", ["", "abc", CID.upper(), CID + "0", CID[:-1], " " + CID, CID + "\n",
                                  "../../etc/passwd", "x" * 10_000, None, 5, b"abc", [CID]])
def test_untrusted_ids_are_replaced_never_echoed(evil):
    got = coerce_correlation_id(evil)
    assert got != evil and validate_correlation_id(got) == got
    with pytest.raises(LogError):
        validate_correlation_id(evil)
    if evil is not None:  # correlation_scope(None) legitimately means "generate one"
        with pytest.raises(LogError), correlation_scope(evil):  # type: ignore[arg-type]
            pass


def test_valid_untrusted_id_is_kept():
    assert coerce_correlation_id(CID) == CID


def test_reserved_and_malformed_field_names_rejected(sink):
    logger, _ = sink
    for bad in ("level", "message", "ts", "correlation_id", "logger", "event", "exc_type", "exc_code"):
        with pytest.raises(LogError, match="reserved"):
            log_event(logger, "INFO", "e", **{bad: 1})
    for bad in ("Upper", "1x", "a-b", "a b", "", "x" * 65, "é"):
        with pytest.raises(LogError, match="lower_snake_case"):
            log_event(logger, "INFO", "e", **{bad: 1})


def test_reserved_names_smuggled_via_stdlib_extra_are_ignored(sink):
    logger, stream = sink
    logger.info("real", extra={FIELDS_ATTR: {"level": "FAKE", "correlation_id": CID, "ts": "x",
                                             "BadName": 1, 7: 2, "good": 3}})
    [record] = lines(stream)
    assert record["level"] == "INFO" and "correlation_id" not in record
    assert record["ts"].endswith("Z") and record["good"] == 3 and "BadName" not in record


@pytest.mark.parametrize("name", ["", "Upper", "a b", "1a", "a-b", "x" * 129, "a\n"])
def test_bad_event_names_rejected(sink, name):
    with pytest.raises(LogError, match="event name"):
        log_event(sink[0], "INFO", name)


def test_bad_levels_rejected(sink):
    for bad in ("info", "TRACE", "", None):
        with pytest.raises(LogError, match="level"):
            log_event(sink[0], bad, "e")  # type: ignore[arg-type]
        with pytest.raises(LogError, match="level"):
            configure_logging(level=bad)  # type: ignore[arg-type]


def test_too_many_fields_rejected(sink):
    log_event(sink[0], "INFO", "e", **{f"f{i}": i for i in range(MAX_FIELDS)})
    with pytest.raises(LogError, match="fields"):
        log_event(sink[0], "INFO", "e", **{f"f{i}": i for i in range(MAX_FIELDS + 1)})


# ---------------------------------------------------- never-raises guarantees
class Evil:
    def __repr__(self):
        raise RuntimeError("repr must not be called")

    def __str__(self):
        raise RuntimeError("str must not be called")


def test_unknown_objects_become_type_names_without_calling_repr(sink):
    logger, stream = sink
    log_event(logger, "INFO", "e", obj=Evil(), nested={"k": [Evil()]}, st={1, 2})
    [record] = lines(stream)
    assert record["obj"] == "<Evil>" and record["nested"] == {"k": ["<Evil>"]} and record["st"] == "<set>"


def test_formatter_never_raises_on_hostile_records():
    """Driven through a bare handler: pytest attaches its own capture handler to
    non-propagating loggers and that one re-raises on unprintable arguments."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    cases = [("%s", (Evil(),)), ("%d", ("not a number",)), ("%s %s", (1,)), ("lone" + chr(0xD800), ())]
    for msg, args in cases:
        handler.handle(logging.LogRecord("n", logging.INFO, "f", 1, msg, args, None))
    out = lines(stream)
    assert len(out) == 4 and all(r["level"] == "INFO" for r in out)
    assert out[0]["message"] == out[1]["message"] == out[2]["message"] == "<unformattable log message>"
    assert out[3]["message"] == "lone\ufffd"


def test_formatter_falls_back_when_rendering_itself_fails():
    class Broken(JsonFormatter):
        def _render(self, record):
            raise RuntimeError("bug in renderer")

    record = logging.LogRecord("n", logging.INFO, "f", 1, "m", None, None)
    line = Broken().format(record)
    assert json.loads(line)["level"] == "ERROR" and "\n" not in line


def test_log_call_with_non_finite_and_huge_numbers(sink):
    logger, stream = sink
    log_event(logger, "INFO", "e", a=float("nan"), b=float("inf"), c=-float("inf"), d=2**80, e=-0.0)
    [record] = lines(stream)
    assert (record["a"], record["b"], record["c"]) == ("NaN", "Infinity", "-Infinity")
    assert record["d"] == str(2**80) and record["e"] == 0


# ------------------------------------------------------------------ boundary
def test_string_truncation_boundary():
    assert sanitize("x" * MAX_STRING_CHARS) == "x" * MAX_STRING_CHARS
    assert sanitize("x" * (MAX_STRING_CHARS + 1)) == "x" * MAX_STRING_CHARS + TRUNCATION_MARK


def test_item_and_depth_boundaries():
    assert len(sanitize(list(range(MAX_ITEMS)))) == MAX_ITEMS
    over = sanitize(list(range(MAX_ITEMS + 1)))
    assert len(over) == MAX_ITEMS + 1 and over[-1] == TRUNCATION_MARK
    deep: object = "leaf"
    for _ in range(MAX_DEPTH + 3):
        deep = [deep]
    flat = json.dumps(sanitize(deep))
    assert "<max-depth>" in flat and flat.count("[") <= MAX_DEPTH + 1
    big = sanitize({str(i): i for i in range(MAX_ITEMS + 5)})
    assert len(big) == MAX_ITEMS + 1 and big["_truncated"] is True


def test_sanitize_handles_lone_surrogates_keys_and_tuples():
    assert sanitize(chr(0xD800)) == "�"
    assert sanitize({1: "a", ("t",): "b"}) == {"<int>": "a", "<tuple>": "b"}
    assert sanitize((1, "a")) == [1, "a"]


def test_error_types():
    assert issubclass(LogError, TrinetraError) and LogError.code == "LOG_INVALID"


# ------------------------------------------------------------------ properties
RESERVED = {"ts", "level", "logger", "message", "event", "correlation_id", "exc_type", "exc_code"}
field_names = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True).filter(lambda k: k not in RESERVED)
field_values = st.one_of(
    st.none(), st.integers(), st.floats(), st.text(max_size=60), st.lists(st.integers(), max_size=5)
)


@given(st.text(max_size=300), st.dictionaries(field_names, field_values, max_size=6))
def test_property_any_message_and_fields_yield_exactly_one_valid_json_line(message, fields):
    stream = io.StringIO()
    name = "trinetra.test.prop"
    handler = configure_logging(level="DEBUG", stream=stream, logger_name=name)
    try:
        logging.getLogger(name).info("%s", message, extra={FIELDS_ATTR: fields})
    finally:
        handler.close()
        logging.getLogger(name).handlers.clear()
    out = stream.getvalue()
    assert out.endswith("\n") and out.count("\n") == 1
    record = json.loads(out)
    assert record["level"] == "INFO" and set(fields) <= set(record)


@given(st.text(max_size=100))
def test_property_coerce_always_returns_a_valid_id(text):
    assert validate_correlation_id(coerce_correlation_id(text))
