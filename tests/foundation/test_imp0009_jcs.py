"""IMP-0009 tests: JCS canonical JSON (RFC 8785)."""

from __future__ import annotations

import json
import math
import re
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trinetra.digest import sha256_digest
from trinetra.errors import TrinetraError
from trinetra.jcs import (
    MAX_DEPTH,
    MAX_NODES,
    MAX_SAFE_INTEGER,
    JcsError,
    canonical_digest,
    canonicalize,
    format_number,
    loads_strict,
)


def double(hex_bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(hex_bits))[0]


# RFC 8785 Appendix B (ES6 number serialization samples): IEEE-754 bits -> text.
RFC_NUMBERS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


# ---------------------------------------------------------------- happy path
@pytest.mark.parametrize(("bits", "text"), RFC_NUMBERS)
def test_rfc8785_appendix_b_number_vectors(bits, text):
    assert format_number(double(bits)) == text


def test_rfc8785_row_becbf647612f3696_is_pinned_to_the_es_closest_rule_and_flagged_unverified():
    """UNVERIFIED against the RFC text (not reachable offline). My recollection of
    the RFC row is '-0.0000033333333333333335'; the ECMAScript 'closest candidate'
    rule and Python's correctly rounded repr both give '...3333'. Both strings
    denote the same double. A reviewer must check Appendix B (REV candidate)."""
    value = double("becbf647612f3696")
    assert format_number(value) == "-0.0000033333333333333333"
    assert float(format_number(value)) == float("-0.0000033333333333333335") == value


def test_rfc8785_section_3_2_4_end_to_end_sample():
    raw = (
        '{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],'
        '"string":"\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        '"literals":[null,true,false]}'
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonicalize(loads_strict(raw)).decode("utf-8") == expected


def test_rfc8785_section_3_2_3_utf16_key_sorting():
    keys = ["€", "\r", "דּ", "1", "\U0001f600", "\u0080", "ö"]
    out = canonicalize({k: 0 for k in keys}).decode("utf-8")
    ordered = list(json.loads(out))
    # UTF-16 code-unit order: CR, '1', U+0080, o-umlaut, euro, emoji (D83D..) BEFORE U+FB33.
    assert ordered == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
    assert sorted(keys) != ordered  # plain code-point order would put the emoji last


def test_basic_shapes_and_no_whitespace():
    value = {"b": [1, 2.5, "x", None, True, False], "a": {"z": 1, "y": []}}
    assert canonicalize(value) == b'{"a":{"y":[],"z":1},"b":[1,2.5,"x",null,true,false]}'
    assert canonicalize([]) == b"[]" and canonicalize({}) == b"{}" and canonicalize("") == b'""'


def test_insertion_order_never_matters():
    a = {"x": 1, "y": {"p": 1, "q": 2}}
    b = {"y": {"q": 2, "p": 1}, "x": 1}
    assert canonicalize(a) == canonicalize(b) and canonical_digest(a) == canonical_digest(b)


def test_canonical_digest_is_sha256_of_canonical_bytes():
    value = {"k": [1, 2, 3]}
    assert canonical_digest(value) == sha256_digest(b'{"k":[1,2,3]}')
    assert canonical_digest({}) == sha256_digest(b"{}")


def test_string_escaping_rules():
    s = "\"\\\b\t\n\f\r\x00\x01\x1f\x7f/ é\U0001f600"
    out = canonicalize(s).decode("utf-8")
    assert out == '"\\"\\\\\\b\\t\\n\\f\\r\\u0000\\u0001\\u001f\x7f/ é\U0001f600"'
    assert json.loads(out) == s


def test_output_is_utf8_not_ascii_escaped():
    assert canonicalize("€") == '"€"'.encode()
    assert canonicalize("\U0001f600") == b'"\xf0\x9f\x98\x80"'


def test_integer_valued_floats_have_no_fraction():
    assert canonicalize([1.0, -0.0, 100.0, 1e21, 1e20, 123456789012345680000.0]) == (
        b"[1,0,100,1e+21,100000000000000000000,123456789012345680000]"
    )


# ----------------------------------------------------------------- boundary
def test_integer_safe_range_edges():
    assert canonicalize(MAX_SAFE_INTEGER) == b"9007199254740991"
    assert canonicalize(-MAX_SAFE_INTEGER) == b"-9007199254740991"
    for bad in (MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1, 2**64):
        with pytest.raises(JcsError, match="I-JSON"):
            canonicalize(bad)
    assert canonicalize(float(MAX_SAFE_INTEGER + 1)) == b"9007199254740992"  # floats are fine


def test_depth_boundary():
    def nest(levels):
        value: object = 1
        for _ in range(levels):
            value = [value]
        return value

    assert canonicalize(nest(MAX_DEPTH)).count(b"[") == MAX_DEPTH
    with pytest.raises(JcsError, match="deeper"):
        canonicalize(nest(MAX_DEPTH + 1))


def test_node_budget_boundary():
    assert canonicalize([0] * (MAX_NODES - 1)).startswith(b"[0,0")
    with pytest.raises(JcsError, match="nodes"):
        canonicalize([0] * MAX_NODES)


def test_exponent_switchover_points():
    assert format_number(1e21) == "1e+21" and format_number(9.99e20) == "999000000000000000000"
    assert format_number(1e-6) == "0.000001" and format_number(1e-7) == "1e-7"
    assert format_number(1.5e-7) == "1.5e-7" and format_number(123e-9) == "1.23e-7"
    assert format_number(-1e21) == "-1e+21" and format_number(5e-324) == "5e-324"


# ----------------------------------------------------------------- negative
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_rejected(bad):
    with pytest.raises(JcsError):
        canonicalize(bad)
    with pytest.raises(JcsError):
        canonicalize({"x": [bad]})
    with pytest.raises(JcsError):
        format_number(bad)


@pytest.mark.parametrize(
    "bad",
    [(1, 2), {1, 2}, b"bytes", bytearray(b"x"), object(), 1 + 2j, frozenset(), range(3), {1: "int key"},
     {None: 1}, {("a",): 1}],
)
def test_unsupported_types_rejected(bad):
    with pytest.raises(JcsError):
        canonicalize(bad)


def test_subclasses_are_rejected_not_silently_accepted():
    class MyDict(dict):
        pass

    class MyList(list):
        pass

    class MyStr(str):
        pass

    class MyInt(int):
        pass

    for bad in (MyDict(), MyList(), MyStr("x"), MyInt(1)):
        with pytest.raises(JcsError, match="unsupported"):
            canonicalize(bad)


@pytest.mark.parametrize(
    "bad", [chr(0xD800), "a" + chr(0xDFFF) + "b", chr(0xD83D), chr(0xDC00) + chr(0xD800)]
)
def test_lone_surrogates_rejected_in_values_and_keys(bad):
    with pytest.raises(JcsError, match="surrogate"):
        canonicalize(bad)
    with pytest.raises(JcsError, match="surrogate"):
        canonicalize({bad: 1})


def test_valid_surrogate_pair_via_escape_is_one_scalar():
    assert canonicalize(loads_strict('"\\ud83d\\ude00"')) == '"\U0001f600"'.encode()


def test_error_reports_a_path_and_is_a_registered_trinetra_error():
    with pytest.raises(JcsError) as info:
        canonicalize({"a": [1, {"b": float("nan")}]})
    assert info.value.details["path"] == "$.a[1].b"
    assert issubclass(JcsError, TrinetraError) and JcsError.code == "JCS_INVALID"


def test_cyclic_structure_is_rejected_by_depth_not_recursion_error():
    cyc: list = []
    cyc.append(cyc)
    with pytest.raises(JcsError, match="deeper"):
        canonicalize(cyc)


# ---------------------------------------------------------- strict parser
@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}', '{"a":{"b":1,"b":1}}', "NaN", "Infinity", "-Infinity", "[NaN]", "1e999", "-1e999",
        '"\\ud800"', '{"\\udfff":1}', "9007199254740993", "-9007199254740993", "[18446744073709551617]",
        "[18446744073709551616]", "1" + "0" * 400,
        "", " ", "{", "[1,]", "{'a':1}", "[01]", "+1", ".5", "1.", "tru", '"unterminated', "[1 2]", "{}{}",
        "﻿{}", '"a\nb"', "\x00",
    ],
)
def test_loads_strict_rejects_hostile_or_malformed_input(text):
    with pytest.raises(JcsError):
        loads_strict(text)


def test_loads_strict_bytes_utf8_rules():
    assert loads_strict(b'{"k":"\xc3\xa9"}') == {"k": "é"}
    for raw in (b"\xff", b'"\xc3"', b"\xef\xbb\xbf{}", b'"\xed\xa0\x80"'):
        with pytest.raises(JcsError):
            loads_strict(raw)


def test_loads_strict_size_and_type_limits():
    assert loads_strict(b"[1]", max_bytes=3) == [1]
    with pytest.raises(JcsError, match="larger"):
        loads_strict(b"[1]", max_bytes=2)
    for bad in (None, 5, [1]):
        with pytest.raises(JcsError, match="bytes or str"):
            loads_strict(bad)  # type: ignore[arg-type]
    for bad in (-1, True, 1.5):
        with pytest.raises(JcsError, match="max_bytes"):
            loads_strict("1", max_bytes=bad)  # type: ignore[arg-type]


def test_loads_strict_deep_nesting_is_rejected_without_crashing():
    with pytest.raises(JcsError):
        loads_strict("[" * (MAX_DEPTH + 5) + "]" * (MAX_DEPTH + 5))
    with pytest.raises(JcsError):
        loads_strict("[" * 100_000 + "]" * 100_000)


def test_integer_literals_beyond_safe_range_follow_double_precision():
    """Accepted iff already the canonical spelling of the double they denote (then
    they are floats); canonical output such as 2**53 must be re-parseable."""
    assert loads_strict("9007199254740992") == 2.0**53 and isinstance(loads_strict("9007199254740992"), float)
    assert canonicalize(loads_strict("[9007199254740992, -9007199254740992]")) == (
        b"[9007199254740992,-9007199254740992]")
    assert canonicalize(loads_strict("18446744073709552000")) == b"18446744073709552000"
    assert canonicalize(loads_strict("57205143117583700")) == b"57205143117583700"
    for bad in ("9007199254740993", "-9007199254740995", "18446744073709551616", "18446744073709551617",
                "1" + "0" * 400):
        with pytest.raises(JcsError):
            loads_strict(bad)


def test_loads_strict_accepts_safe_edges_and_negative_zero():
    assert loads_strict("9007199254740991") == MAX_SAFE_INTEGER
    assert loads_strict("-0") == 0 and canonicalize(loads_strict("[-0, -0.0, 0.0]")) == b"[0,0,0]"
    assert loads_strict(" \n\t[ 1 , 2 ]\r\n") == [1, 2]


# ------------------------------------------------------------------ properties
json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)
    | st.floats(allow_nan=False, allow_infinity=False) | st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)), max_size=12),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=6), children, max_size=4),
    max_leaves=20,
)


@given(json_values)
def test_property_canonical_form_is_idempotent_and_stdlib_parseable(value):
    once = canonicalize(value)
    again = canonicalize(loads_strict(once))
    assert once == again
    # JSON numbers are doubles: compare under double semantics (stdlib would read 57205143117583700 as an exact int).
    assert json.loads(once, parse_int=float) == json.loads(json.dumps(value), parse_int=float)


@given(json_values)
def test_property_canonical_bytes_have_no_insignificant_whitespace(value):
    text = canonicalize(value).decode("utf-8")
    structure = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)  # drop string literals
    assert not re.search(r"\s", structure)
    assert "\n" not in text and "\r" not in text and "\t" not in text  # also escaped inside strings


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_property_number_text_round_trips_to_the_same_double(x):
    text = format_number(x)
    assert float(text) == x
    assert "E" not in text and "+" not in text.replace("e+", "")
    if x == 0:
        assert text == "0"


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_property_number_text_matches_es_shape(x):
    text = format_number(x).lstrip("-")
    if "e" in text:
        mantissa, exp = text.split("e")
        assert exp[0] in "+-" and exp[1] != "0"
        assert "." not in mantissa or not mantissa.endswith("0")
    elif "." in text:
        assert not text.endswith("0") and not text.endswith(".")
    if text != "0":
        assert math.isfinite(float(text))


@given(st.dictionaries(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=5),
                       st.integers(0, 9), min_size=2, max_size=8))
def test_property_keys_are_sorted_by_utf16_code_units(mapping):
    keys = list(json.loads(canonicalize(mapping)))
    assert keys == sorted(keys, key=lambda k: k.encode("utf-16-be"))
