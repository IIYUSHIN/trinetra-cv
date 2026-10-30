"""IMP-0007 tests: UTC timestamp and monotonic-time primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trinetra.errors import TrinetraError
from trinetra.timeutil import (
    MAX_EPOCH_SECONDS,
    MAX_NS,
    Clock,
    FixedClock,
    ManualClock,
    Stopwatch,
    SystemClock,
    TimeError,
    elapsed_ns,
    epoch_seconds,
    format_utc,
    parse_utc,
    require_utc,
    to_utc,
    truncate_to_seconds,
    utc_from_epoch_seconds,
)

T0 = datetime(2026, 9, 2, 14, 35, 20, tzinfo=UTC)


# ---------------------------------------------------------------- happy path
def test_bible_receipt_literal_round_trips():
    """The one timestamp literal in the Bible (receipt example, line 16601)."""
    assert parse_utc("2026-09-02T14:35:20Z") == T0
    assert format_utc(T0) == "2026-09-02T14:35:20Z"


def test_microsecond_variant_round_trips():
    moment = T0.replace(microsecond=5)
    assert format_utc(moment, precision="microseconds") == "2026-09-02T14:35:20.000005Z"
    assert parse_utc("2026-09-02T14:35:20.000005Z") == moment
    assert format_utc(T0, precision="microseconds") == "2026-09-02T14:35:20.000000Z"


def test_parse_result_is_aware_utc():
    parsed = parse_utc("2026-09-02T14:35:20Z")
    assert parsed.tzinfo is UTC and parsed.utcoffset() == timedelta(0)


def test_epoch_helpers():
    assert utc_from_epoch_seconds(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert epoch_seconds(T0) == 1_788_359_720
    assert utc_from_epoch_seconds(epoch_seconds(T0)) == T0


def test_to_utc_converts_explicitly_and_require_utc_does_not():
    plus5 = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 9, 2, 20, 5, 20, tzinfo=plus5)
    assert to_utc(local) == T0 and to_utc(local).tzinfo is UTC
    with pytest.raises(TimeError, match="UTC"):
        require_utc(local)


def test_zero_offset_non_utc_tzinfo_is_normalised():
    zero = timezone(timedelta(0), "Zulu")
    assert require_utc(T0.replace(tzinfo=zero)).tzinfo is UTC


def test_truncate_to_seconds_is_explicit():
    moment = T0.replace(microsecond=999_999)
    with pytest.raises(TimeError, match="sub-second"):
        format_utc(moment)
    assert format_utc(truncate_to_seconds(moment)) == "2026-09-02T14:35:20Z"


# ----------------------------------------------------------------- boundary
def test_range_extremes():
    assert format_utc(datetime(1, 1, 1, tzinfo=UTC)) == "0001-01-01T00:00:00Z"
    assert format_utc(datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)) == "9999-12-31T23:59:59Z"
    assert parse_utc("9999-12-31T23:59:59.999999Z").microsecond == 999_999
    assert utc_from_epoch_seconds(MAX_EPOCH_SECONDS) == datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert epoch_seconds(utc_from_epoch_seconds(MAX_EPOCH_SECONDS)) == MAX_EPOCH_SECONDS
    for bad in (-1, MAX_EPOCH_SECONDS + 1):
        with pytest.raises(TimeError):
            utc_from_epoch_seconds(bad)


def test_leap_day_rules():
    assert parse_utc("2024-02-29T00:00:00Z").day == 29
    for bad in ("2023-02-29T00:00:00Z", "1900-02-29T00:00:00Z"):
        with pytest.raises(TimeError, match="real instant"):
            parse_utc(bad)
    assert parse_utc("2000-02-29T00:00:00Z").day == 29


def test_before_epoch_datetime_cannot_become_epoch_seconds():
    with pytest.raises(TimeError, match="epoch"):
        epoch_seconds(datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC))


# ----------------------------------------------------------------- negative
@pytest.mark.parametrize(
    "text",
    [
        "", " 2026-09-02T14:35:20Z", "2026-09-02T14:35:20Z ", "2026-09-02T14:35:20Z\n",
        "2026-09-02t14:35:20Z", "2026-09-02T14:35:20z", "2026-09-02 14:35:20Z",
        "2026-09-02T14:35:20", "2026-09-02T14:35:20+00:00", "2026-09-02T14:35:20-00:00",
        "2026-09-02T14:35:20+05:30", "2026-09-02T14:35Z", "2026-09-02T14:35:20.5Z",
        "2026-09-02T14:35:20.1234567Z", "2026-09-02T14:35:20.Z", "2026-09-02T14:35:20,000000Z",
        "20260902T143520Z", "2026-9-2T14:35:20Z", "26-09-02T14:35:20Z", "+2026-09-02T14:35:20Z",
        "２０２６-09-02T14:35:20Z", "2026-09-02T14:35:20Z\x00", "2026-W36-3T14:35:20Z",
        "2026-13-02T14:35:20Z", "2026-00-02T14:35:20Z", "2026-09-00T14:35:20Z",
        "2026-09-31T14:35:20Z", "2026-09-02T24:00:00Z", "2026-09-02T14:60:20Z",
        "2026-09-02T23:59:60Z", "0000-01-01T00:00:00Z",
    ],
)
def test_malformed_timestamps_rejected(text):
    with pytest.raises(TimeError):
        parse_utc(text)


@pytest.mark.parametrize("value", [None, 5, b"2026-09-02T14:35:20Z", T0, ["x"]])
def test_non_string_parse_rejected(value):
    with pytest.raises(TimeError, match="string"):
        parse_utc(value)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 9, 2, 14, 35, 20), "2026-09-02T14:35:20Z", None, 5, T0.date(),
     datetime(2026, 9, 2, tzinfo=timezone(timedelta(hours=1)))],
)
def test_require_utc_rejects_naive_wrong_type_and_offsets(value):
    with pytest.raises(TimeError):
        require_utc(value)
    with pytest.raises(TimeError):
        format_utc(value)  # type: ignore[arg-type]


def test_datetime_subclasses_are_rejected():
    class Stamped(datetime):
        pass

    sub = Stamped(2026, 9, 2, 14, 35, 20, tzinfo=UTC)
    with pytest.raises(TimeError, match="datetime"):
        require_utc(sub)


def test_naive_never_converted_by_to_utc():
    with pytest.raises(TimeError, match="aware"):
        to_utc(datetime(2026, 9, 2, 14, 35, 20))


def test_to_utc_overflow_is_a_time_error():
    edge = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(TimeError, match="range"):
        to_utc(edge)


@pytest.mark.parametrize("seconds", [True, 1.0, "1", None, 1.5])
def test_epoch_seconds_type_strict(seconds):
    with pytest.raises(TimeError):
        utc_from_epoch_seconds(seconds)


def test_bad_precision_rejected():
    with pytest.raises(TimeError, match="precision"):
        format_utc(T0, precision="ms")  # type: ignore[arg-type]


def test_time_error_is_a_registered_trinetra_error():
    assert issubclass(TimeError, TrinetraError) and TimeError.code == "TIME_INVALID"
    assert TimeError("x").to_dict()["error_code"] == "TIME_INVALID"


# ------------------------------------------------------- clocks / monotonic
def test_fixed_clock_never_moves():
    clock = FixedClock(T0, monotonic_ns=7)
    assert (clock.now(), clock.monotonic_ns()) == (T0, 7)
    assert (clock.now(), clock.monotonic_ns()) == (T0, 7)


def test_manual_clock_advances_both_clocks():
    clock = ManualClock(T0, monotonic_ns=1_000)
    clock.advance(timedelta(seconds=2, microseconds=500))
    assert clock.now() == T0 + timedelta(seconds=2, microseconds=500)
    assert clock.monotonic_ns() == 1_000 + 2_000_500_000


def test_wall_jump_backwards_does_not_affect_elapsed_time():
    clock = ManualClock(T0)
    watch = Stopwatch(clock)
    clock.advance(timedelta(seconds=5))
    clock.set_wall(T0 - timedelta(days=1))  # NTP step back / operator change
    assert watch.elapsed_ns() == 5_000_000_000
    assert clock.now() < T0


def test_stopwatch_is_monotone_under_repeated_reads():
    clock = ManualClock(T0)
    watch = Stopwatch(clock)
    readings = []
    for _ in range(5):
        readings.append(watch.elapsed_ns())
        clock.advance(timedelta(milliseconds=3))
    assert readings == sorted(readings) and readings[-1] == 12_000_000


def test_stopwatch_detects_a_clock_that_goes_backwards():
    class Broken:
        def __init__(self):
            self.values = iter([100, 50])

        def now(self):
            return T0

        def monotonic_ns(self):
            return next(self.values)

    watch = Stopwatch(Broken())
    with pytest.raises(TimeError, match="reversed"):
        watch.elapsed_ns()


def test_system_clock_conforms_and_is_sane():
    clock: Clock = SystemClock()
    first, later = clock.monotonic_ns(), clock.monotonic_ns()
    assert 0 <= first <= later
    assert clock.now().tzinfo is UTC and abs(clock.now() - datetime.now(UTC)) < timedelta(minutes=1)
    assert format_utc(truncate_to_seconds(clock.now())).endswith("Z")


@pytest.mark.parametrize("delta", [timedelta(seconds=-1), 5, 1.5, None])
def test_manual_clock_cannot_go_backwards_by_advance(delta):
    with pytest.raises(TimeError):
        ManualClock(T0).advance(delta)  # type: ignore[arg-type]


def test_advance_overflow_leaves_clock_unchanged():
    clock = ManualClock(datetime(9999, 12, 31, tzinfo=UTC), monotonic_ns=5)
    with pytest.raises(TimeError, match="range"):
        clock.advance(timedelta(days=2))
    assert clock.monotonic_ns() == 5 and clock.now() == datetime(9999, 12, 31, tzinfo=UTC)
    near = ManualClock(T0, monotonic_ns=MAX_NS - 1)
    with pytest.raises(TimeError):
        near.advance(timedelta(seconds=1))
    assert near.now() == T0 and near.monotonic_ns() == MAX_NS - 1


@pytest.mark.parametrize("ns", [-1, MAX_NS + 1, True, 1.0, "1", None])
def test_monotonic_readings_are_validated(ns):
    with pytest.raises(TimeError):
        FixedClock(T0, monotonic_ns=ns)  # type: ignore[arg-type]
    with pytest.raises(TimeError):
        elapsed_ns(0, ns)


def test_elapsed_ns_boundaries():
    assert elapsed_ns(0, 0) == 0 and elapsed_ns(0, MAX_NS) == MAX_NS
    with pytest.raises(TimeError, match="reversed"):
        elapsed_ns(1, 0)


def test_clocks_reject_naive_wall_time():
    for cls in (FixedClock, ManualClock):
        with pytest.raises(TimeError):
            cls(datetime(2026, 1, 1))
    with pytest.raises(TimeError):
        ManualClock(T0).set_wall(datetime(2026, 1, 1))


# ----------------------------------------------------------------- properties
@given(st.datetimes(min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59, 999999)))
def test_property_format_parse_round_trip(naive):
    moment = naive.replace(tzinfo=UTC)
    assert parse_utc(format_utc(moment, precision="microseconds")) == moment
    whole = moment.replace(microsecond=0)
    assert parse_utc(format_utc(whole)) == whole


@given(st.datetimes(min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59, 999999)))
def test_property_format_is_fixed_width_and_sortable(naive):
    text = format_utc(naive.replace(tzinfo=UTC, microsecond=0))
    assert len(text) == 20 and text[10] == "T" and text.endswith("Z")


@given(st.integers(0, MAX_EPOCH_SECONDS))
def test_property_epoch_round_trip(seconds):
    assert epoch_seconds(utc_from_epoch_seconds(seconds)) == seconds


@given(st.text(max_size=40))
def test_property_parse_never_raises_anything_but_time_error(text):
    try:
        parsed = parse_utc(text)
    except TimeError:
        return
    assert format_utc(parsed, precision="microseconds" if "." in text else "seconds") == text


@given(st.integers(0, 10**12), st.integers(0, 10**12))
def test_property_elapsed_matches_difference(a, b):
    lo, hi = sorted((a, b))
    assert elapsed_ns(lo, hi) == hi - lo
    if lo != hi:
        with pytest.raises(TimeError):
            elapsed_ns(hi, lo)
