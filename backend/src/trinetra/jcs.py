"""JCS canonical JSON utility (IMP-0009): RFC 8785 for Python values.

Standard library only. Produces the UTF-8 bytes whose SHA-256 identifies
records that the Bible specifies as "JCS UTF-8 + SHA-256".

IMPORTANT (Bible line 423): JCS must NOT be substituted for TIIR wire
identity. This module is for JCS-specified records (e.g. ReleaseManifest,
AssessmentJobSpec digests) only.

Accepted values: ``None``, ``bool``, ``int`` (I-JSON safe range only), finite
``float``, ``str`` (no lone surrogates), ``list`` and ``dict`` with ``str``
keys. Everything else - tuples, sets, bytes, NaN/Infinity, dict subclasses,
non-string keys, over-deep or over-large structures - is rejected rather than
guessed at. Tuples are rejected deliberately: callers convert explicitly.

The strict parser rejects what `json.loads` silently accepts: duplicate
object members, NaN/Infinity, integers outside the I-JSON range, non-finite
floats, lone surrogates, a UTF-8 BOM, invalid UTF-8 and over-deep nesting.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any, Final

from trinetra.digest import sha256_digest
from trinetra.errors import TrinetraError

MAX_SAFE_INTEGER: Final = 2**53 - 1
MAX_DEPTH: Final = 64
MAX_NODES: Final = 1_000_000
DEFAULT_MAX_INPUT_BYTES: Final = 16 * 1024 * 1024
_SURROGATE_RE: Final = re.compile("[\ud800-\udfff]")
_ESCAPES: Final = {
    0x22: '\\"', 0x5C: "\\\\", 0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
}


class JcsError(TrinetraError):
    code = "JCS_INVALID"
    default_message = "value cannot be canonicalized as JCS"


def _fail(message: str, path: str) -> JcsError:
    return JcsError(f"{message} at {path}", details={"path": path})


def _string(text: str, path: str) -> str:
    if _SURROGATE_RE.search(text):
        raise _fail("lone surrogate in string", path)
    out = ['"']
    for ch in text:
        code = ord(ch)
        if code in _ESCAPES:
            out.append(_ESCAPES[code])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def format_number(value: float) -> str:
    """ECMAScript ``Number::toString`` (RFC 8785 section 3.2.2.3) for a finite float."""
    if not math.isfinite(value):
        raise JcsError("NaN and Infinity cannot be represented", details={"path": "$"})
    if value == 0:
        return "0"  # also folds -0.0
    sign = "-" if value < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(map(str, digit_tuple)).rstrip("0")
    # value = 0.DIGITS x 10**n  (digits has no trailing zeros; value != 0)
    k = len(digits)
    n = len("".join(map(str, digit_tuple))) + int(exponent)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body


def _encode(value: Any, path: str, depth: int, budget: list[int]) -> str:
    budget[0] -= 1
    if budget[0] < 0:
        raise _fail(f"more than {MAX_NODES} nodes", path)
    if depth > MAX_DEPTH:
        raise _fail(f"nesting deeper than {MAX_DEPTH}", path)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    kind = type(value)
    if kind is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise _fail("integer outside the I-JSON safe range", path)
        return str(value)
    if kind is float:
        try:
            return format_number(value)
        except JcsError as exc:
            raise _fail("non-finite number", path) from exc
    if kind is str:
        return _string(value, path)
    if kind is list:
        return "[" + ",".join(
            _encode(item, f"{path}[{i}]", depth + 1, budget) for i, item in enumerate(value)
        ) + "]"
    if kind is dict:
        for key in value:
            if type(key) is not str:
                raise _fail(f"object key is {type(key).__name__}, not str", path)
        ordered = sorted(value, key=_utf16_key)
        for key in ordered:
            if _SURROGATE_RE.search(key):
                raise _fail("lone surrogate in object key", path)
        return "{" + ",".join(
            _string(key, path) + ":" + _encode(value[key], f"{path}.{key}", depth + 1, budget)
            for key in ordered
        ) + "}"
    raise _fail(f"unsupported type {kind.__name__}", path)


def _utf16_key(key: str) -> bytes:
    # RFC 8785 3.2.3: sort by UTF-16 code units. Big-endian bytes compare identically.
    try:
        return key.encode("utf-16-be")
    except UnicodeEncodeError:
        return key.encode("utf-16-be", "surrogatepass")  # reported as a surrogate error later


def canonicalize(value: Any) -> bytes:
    """RFC 8785 canonical form of `value`, UTF-8 encoded."""
    return _encode(value, "$", 0, [MAX_NODES]).encode("utf-8")


def canonical_digest(value: Any) -> str:
    """``sha256:<hex>`` of the canonical bytes (no domain separation added)."""
    return sha256_digest(canonicalize(value))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in pairs:
        if key in out:
            raise JcsError("duplicate object member", details={"path": f"$.{key}"})
        out[key] = val
    return out


def _reject_constant(name: str) -> Any:
    raise JcsError(f"{name} is not valid JSON", details={"path": "$"})


def _parse_int(text: str) -> int | float:
    """Safe-range integers stay `int`. Beyond that, JSON numbers are doubles, and
    the literal is accepted only if it already is the canonical ES spelling of
    the double it denotes (so canonical output such as 9007199254740992 or
    57205143117583700 is re-parseable, while 9007199254740993 - which would
    silently become ...992 - is rejected)."""
    value = int(text)
    if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        return value
    try:
        as_float = float(value)
        canonical = format_number(as_float)
    except (OverflowError, JcsError) as exc:
        raise JcsError("integer too large for a double", details={"path": "$"}) from exc
    if canonical != str(value):
        raise JcsError("integer would lose precision as a double", details={"path": "$"})
    return as_float


def _parse_float(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise JcsError("number overflows to Infinity", details={"path": "$"})
    return value


def loads_strict(data: bytes | str, *, max_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> Any:
    """Parse untrusted JSON text strictly, then validate it is JCS-representable."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise JcsError("max_bytes must be a non-negative int", details={"path": "max_bytes"})
    if isinstance(data, bytes | bytearray):
        if len(data) > max_bytes:
            raise JcsError("input larger than max_bytes", details={"path": "$"})
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JcsError("input is not valid UTF-8", details={"path": "$"}) from exc
    elif isinstance(data, str):
        if len(data.encode("utf-8", "surrogatepass")) > max_bytes:
            raise JcsError("input larger than max_bytes", details={"path": "$"})
        text = data
    else:
        raise JcsError("input must be bytes or str", details={"path": "$"})
    if text.startswith("﻿"):
        raise JcsError("byte order mark is not allowed", details={"path": "$"})
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
    except JcsError:
        raise
    except RecursionError as exc:
        raise JcsError("nesting too deep", details={"path": "$"}) from exc
    except ValueError as exc:  # JSONDecodeError subclasses ValueError
        raise JcsError("malformed JSON", details={"path": "$"}) from exc
    _encode(value, "$", 0, [MAX_NODES])  # depth/size/surrogate validation
    return value
