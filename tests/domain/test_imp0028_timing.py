"""IMP-0028 tests: UtcTimestamp and bounded Duration."""

from __future__ import annotations

import copy
import pickle
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.base import DomainModel
from trinetra.domain.timing import MAX_DURATION_US, Duration, TimingError, UtcTimestamp

WHOLE = "2026-09-02T14:35:20Z"
MICRO = "2026-09-02T14:35:20.250000Z"


# ---------------------------------------------------------------- UtcTimestamp
def test_parse_format_roundtrip_with_one_spelling_per_instant():
    assert UtcTimestamp.parse(WHOLE).to_wire() == WHOLE == str(UtcTimestamp.parse(WHOLE))
    assert UtcTimestamp.parse(MICRO).to_wire() == MICRO
    assert UtcTimestamp.parse(WHOLE) == UtcTimestamp(datetime(2026, 9, 2, 14, 35, 20, tzinfo=UTC))
    assert repr(UtcTimestamp.parse(WHOLE)) == f"UtcTimestamp({WHOLE!r})"


@pytest.mark.parametrize(
    "bad",
    ["2026-09-02T14:35:20.000000Z",  # second spelling of a whole second
     "2026-09-02T14:35:20+00:00", "2026-09-02 14:35:20Z", "2026-09-02T14:35:20", "2026-09-02T14:35Z",
     "2026-13-02T14:35:20Z", "2026-02-30T00:00:00Z", "2026-09-02T24:00:00Z", "2026-09-02T14:35:60Z",
     "2026-09-02T14:35:20.5Z", "2026-09-02T14:35:20z", " " + WHOLE, WHOLE + " ", WHOLE + "\n", "",
     "1969-12-31T23:59:59Z", None, 5, b"x", datetime(2026, 1, 1, tzinfo=UTC)],
)
def test_parse_rejects_non_canonical_or_invalid(bad):
    with pytest.raises(TimingError):
        UtcTimestamp.parse(bad)


def test_boundaries_epoch_and_max():
    assert UtcTimestamp.parse("1970-01-01T00:00:00Z").to_wire() == "1970-01-01T00:00:00Z"
    last = UtcTimestamp.parse("9999-12-31T23:59:59.999999Z")
    assert last.to_wire() == "9999-12-31T23:59:59.999999Z"
    with pytest.raises(TimingError):
        last.plus(Duration(1))


@pytest.mark.parametrize(
    "moment",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))), "2026", None, 5,
     datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)],
)
def test_constructor_requires_aware_utc_in_range(moment):
    with pytest.raises(TimingError):
        UtcTimestamp(moment)


def test_ordering_is_typed_and_total():
    a, b = UtcTimestamp.parse(WHOLE), UtcTimestamp.parse(MICRO)
    assert a < b and b > a and a <= UtcTimestamp.parse(WHOLE) and b >= UtcTimestamp.parse(MICRO) and a != b
    with pytest.raises(TypeError, match="cannot order"):
        _ = a < datetime(2030, 1, 1, tzinfo=UTC)  # type: ignore[operator]
    with pytest.raises(TypeError, match="cannot order"):
        _ = a < Duration(1)  # type: ignore[operator]
    assert (a == a.moment) is False and hash(a) == hash(UtcTimestamp.parse(WHOLE))


def test_immutable_copy_pickle():
    a = UtcTimestamp.parse(MICRO)
    with pytest.raises(AttributeError):
        a._dt = datetime(2000, 1, 1, tzinfo=UTC)
    assert copy.copy(a) is a and copy.deepcopy(a) is a
    assert pickle.loads(pickle.dumps(a)) == a


# -------------------------------------------------------------------- Duration
def test_duration_basics_and_boundaries():
    assert Duration(0).microseconds == 0 and Duration(MAX_DURATION_US).microseconds == MAX_DURATION_US
    assert Duration.from_seconds(2).microseconds == 2_000_000 and Duration(5).to_wire() == 5
    for bad in (-1, MAX_DURATION_US + 1):
        with pytest.raises(TimingError):
            Duration(bad)


@pytest.mark.parametrize("bad", [True, 1.0, "1", None, b"1", Duration(1)])
def test_duration_requires_plain_int(bad):
    with pytest.raises(TimingError):
        Duration(bad)


@pytest.mark.parametrize("bad", [-1, True, 1.5, "1", None])
def test_from_seconds_requires_non_negative_int(bad):
    with pytest.raises(TimingError):
        Duration.from_seconds(bad)


def test_duration_ordering_equality_hash():
    assert Duration(1) < Duration(2) and Duration(2) >= Duration(2) and Duration(1) == Duration(1)
    assert hash(Duration(1)) == hash(Duration(1)) and (Duration(1) == 1) is False
    with pytest.raises(TypeError, match="cannot order"):
        _ = Duration(1) < 2  # type: ignore[operator]


# ------------------------------------------------------------------ arithmetic
def test_plus_and_until_are_exact_inverses():
    a = UtcTimestamp.parse(WHOLE)
    d = Duration(1_250_000)
    b = a.plus(d)
    assert b.to_wire() == "2026-09-02T14:35:21.250000Z" and a.until(b) == d
    assert a.until(a) == Duration(0)
    with pytest.raises(TimingError, match="earlier"):
        b.until(a)


# ------------------------------------------------------------- inside a model
class Window(DomainModel):
    start: UtcTimestamp
    timeout: Duration


def test_model_wire_is_plain_json_and_strict():
    w = Window(start=UtcTimestamp.parse(MICRO), timeout=Duration.from_seconds(30))
    assert w.to_wire() == {"start": MICRO, "timeout": 30_000_000}
    assert Window(**w.to_wire()) == w
    assert w.canonical_bytes() == f'{{"start":"{MICRO}","timeout":30000000}}'.encode()
    bad_inputs = (
        {"start": "2026-09-02T14:35:20.000000Z", "timeout": 1}, {"start": WHOLE, "timeout": -1},
        {"start": WHOLE, "timeout": True}, {"start": 5, "timeout": 1}, {"start": WHOLE, "timeout": 1.0},
    )
    for bad in bad_inputs:
        with pytest.raises(ValidationError):
            Window(**bad)


@given(st.datetimes(min_value=datetime(1970, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59, 999999)))
def test_property_every_instant_has_one_spelling_that_roundtrips(naive):
    ts = UtcTimestamp(naive.replace(tzinfo=UTC))
    text = ts.to_wire()
    assert UtcTimestamp.parse(text) == ts and (".000000" not in text)
    assert text.endswith("Z") and len(text) in (20, 27)


@given(st.integers(0, 10**12), st.integers(0, 10**12))
def test_property_until_inverts_plus(start_us, delta_us):
    a = UtcTimestamp(datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=start_us))
    d = Duration(delta_us)
    assert a.until(a.plus(d)) == d
