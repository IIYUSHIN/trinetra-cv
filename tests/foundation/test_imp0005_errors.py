"""IMP-0005 tests: root exception hierarchy and stable error-code base."""

from __future__ import annotations

import copy
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trinetra import errors
from trinetra.errors import (
    MAX_CODE_LENGTH,
    MAX_DETAILS_DEPTH,
    MAX_DETAILS_NODES,
    MAX_MESSAGE_LENGTH,
    MAX_SAFE_INTEGER,
    TrinetraError,
    qualified_name,
    registered_error_codes,
    validate_error_code,
)

GOLDEN = Path(__file__).parent / "golden" / "error_codes.json"


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Test-local subclasses must not leak into the process-wide registry."""
    monkeypatch.setattr(errors, "_REGISTRY", dict(errors._REGISTRY))


# ---------------------------------------------------------------- happy path
def test_root_error_defaults():
    err = TrinetraError()
    assert err.code == "TRINETRA_ERROR" and err.retryable is False
    assert err.message == "TRINETRA error" and dict(err.details) == {}
    assert str(err) == "TRINETRA_ERROR: TRINETRA error"
    assert isinstance(err, Exception)


def test_subclass_registers_and_serialises():
    class AssetNotFound(TrinetraError):
        code = "ASSET_NOT_FOUND"
        retryable = False
        default_message = "asset not found"

    err = AssetNotFound(details={"asset_id": "a1", "tries": 2, "ratio": 0.5, "tags": ["x", None]})
    assert registered_error_codes()["ASSET_NOT_FOUND"] is AssetNotFound
    assert err.to_dict() == {
        "error_code": "ASSET_NOT_FOUND",
        "message": "asset not found",
        "retryable": False,
        "details": {"asset_id": "a1", "tries": 2, "ratio": 0.5, "tags": ["x", None]},
    }
    json.dumps(err.to_dict())  # JSON-safe


def test_details_omitted_from_envelope_when_empty():
    assert "details" not in TrinetraError("m").to_dict()


def test_retryable_is_class_level_and_reported():
    class Busy(TrinetraError):
        code = "STORE_BUSY"
        retryable = True

    assert Busy().to_dict()["retryable"] is True


def test_exception_chaining_is_preserved():
    class Wrapped(TrinetraError):
        code = "WRAPPED_FAILURE"

    with pytest.raises(Wrapped) as info:
        try:
            raise OSError("disk")
        except OSError as exc:
            raise Wrapped("could not write") from exc
    assert isinstance(info.value.__cause__, OSError)


def test_abstract_intermediate_is_not_registered_or_instantiable():
    class Family(TrinetraError, abstract=True):
        pass

    class Member(Family):
        code = "FAMILY_MEMBER"

    assert "FAMILY_MEMBER" in registered_error_codes()
    assert issubclass(Member, Family) and isinstance(Member(), Family)
    with pytest.raises(TypeError, match="abstract"):
        Family()


def test_details_are_immutable_and_isolated_from_the_caller():
    source = {"k": ["a", {"z": 1}]}
    err = TrinetraError("m", details=source)
    source["k"].append("mutated")
    source["new"] = 1
    assert err.to_dict()["details"] == {"k": ["a", {"z": 1}]}
    with pytest.raises(TypeError):
        err.details["k"] = 1  # type: ignore[index]
    snapshot = err.to_dict()
    snapshot["details"]["k"].append("x")
    assert err.to_dict()["details"] == {"k": ["a", {"z": 1}]}


# ------------------------------------------------------------------ boundary
@pytest.mark.parametrize("code", ["A", "ASSET_NOT_FOUND", "A1", "X" * MAX_CODE_LENGTH, "E_2_X"])
def test_valid_codes(code):
    assert validate_error_code(code) == code


@pytest.mark.parametrize(
    "code",
    ["", "a", "Asset", "asset_not_found", "_A", "A_", "A__B", "A-B", "A B", "1A", "É",
     "A\n", "X" * (MAX_CODE_LENGTH + 1), None, 5, b"A"],
)
def test_invalid_codes_rejected(code):
    with pytest.raises(ValueError):
        validate_error_code(code)


def test_message_length_boundary():
    TrinetraError("x" * MAX_MESSAGE_LENGTH)
    with pytest.raises(ValueError, match="longer"):
        TrinetraError("x" * (MAX_MESSAGE_LENGTH + 1))


def test_details_integer_boundary():
    TrinetraError(details={"n": MAX_SAFE_INTEGER, "m": -MAX_SAFE_INTEGER})
    for bad in (MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1):
        with pytest.raises(ValueError, match="I-JSON"):
            TrinetraError(details={"n": bad})


def test_details_depth_boundary():
    def nest(levels):
        value = "leaf"
        for _ in range(levels):
            value = {"k": value}
        return value

    TrinetraError(details=nest(MAX_DETAILS_DEPTH))
    with pytest.raises(ValueError, match="deeper"):
        TrinetraError(details=nest(MAX_DETAILS_DEPTH + 1))


def test_details_node_budget_boundary():
    TrinetraError(details={"xs": list(range(MAX_DETAILS_NODES - 2))})
    with pytest.raises(ValueError, match="nodes"):
        TrinetraError(details={"xs": list(range(MAX_DETAILS_NODES))})


# ------------------------------------------------------------------ negative
def test_subclass_without_own_code_is_rejected():
    class First(TrinetraError):
        code = "FIRST_CODE"

    with pytest.raises(TypeError, match="declare its own"):
        class Second(First):  # inherits FIRST_CODE: would silently alias it
            pass


def test_duplicate_code_is_rejected():
    class One(TrinetraError):
        code = "DUPLICATE_CODE"

    with pytest.raises(TypeError, match="already registered"):
        class Two(TrinetraError):
            code = "DUPLICATE_CODE"

    assert registered_error_codes()["DUPLICATE_CODE"] is One


def test_code_colliding_with_root_is_rejected():
    with pytest.raises(TypeError, match="already registered"):
        class Imposter(TrinetraError):
            code = "TRINETRA_ERROR"


@pytest.mark.parametrize("bad", ["lower_case", "", "Mixed_Case", "TRAILING_", "A" * 65])
def test_malformed_code_on_subclass_is_rejected_and_not_registered(bad):
    before = dict(registered_error_codes())
    with pytest.raises(TypeError):
        type("Bad", (TrinetraError,), {"code": bad})
    assert dict(registered_error_codes()) == before


def test_non_bool_retryable_is_rejected():
    with pytest.raises(TypeError, match="retryable"):
        class Weird(TrinetraError):
            code = "WEIRD_RETRY"
            retryable = "yes"  # type: ignore[assignment]


@pytest.mark.parametrize(
    "details",
    [
        {"x": float("nan")}, {"x": float("inf")}, {"x": -float("inf")},
        {"x": b"bytes"}, {"x": {1, 2}}, {"x": object()}, {1: "int key"},
        {"x": {"y": {None: 1}}},
    ],
)
def test_unsafe_details_rejected(details):
    with pytest.raises(ValueError):
        TrinetraError(details=details)


@pytest.mark.parametrize("details", [[("a", 1)], "text", 5, ["x"]])
def test_non_mapping_details_rejected(details):
    with pytest.raises(ValueError, match="mapping"):
        TrinetraError(details=details)


@pytest.mark.parametrize("message", [b"bytes", 5, ["x"], object()])
def test_non_string_message_rejected(message):
    with pytest.raises(ValueError, match="message must be str"):
        TrinetraError(message)  # type: ignore[arg-type]


def test_mutating_class_code_after_definition_does_not_corrupt_registry():
    class Stable(TrinetraError):
        code = "STABLE_CODE"

    Stable.code = "CHANGED_LATER"  # type: ignore[misc]
    assert registered_error_codes()["STABLE_CODE"] is Stable
    assert "CHANGED_LATER" not in registered_error_codes()


# --------------------------------------------------- serialisation / copying
class Portable(TrinetraError):
    """Module-level so pickle can find it; module is not trinetra.*, so the
    golden manifest ignores it."""

    code = "TEST_PORTABLE_ERROR"
    retryable = True


def test_deepcopy_and_pickle_round_trip_keep_code_message_details():
    original = Portable("boom", details={"a": [1, 2], "b": {"c": None}})
    for clone in (copy.deepcopy(original), pickle.loads(pickle.dumps(original))):
        assert type(clone) is Portable
        assert clone.to_dict() == original.to_dict()


@given(
    st.text(min_size=0, max_size=50),
    st.dictionaries(
        st.text(max_size=8),
        st.one_of(st.none(), st.booleans(), st.integers(-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER),
                  st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=12)),
        max_size=8,
    ),
)
def test_property_round_trip_is_lossless_and_json_safe(message, details):
    err = TrinetraError(message, details=details)
    envelope = err.to_dict()
    assert json.loads(json.dumps(envelope, allow_nan=False)) == envelope
    assert envelope["message"] == message
    assert envelope.get("details", {}) == details


# ------------------------------------------------------ stability (golden)
def _import_every_production_module():
    import importlib
    import pkgutil

    import trinetra

    for info in pkgutil.walk_packages(trinetra.__path__, "trinetra."):
        importlib.import_module(info.name)


def test_golden_manifest_matches_registered_production_codes():
    """Stable means stable: a released code may not vanish, move or be renamed,
    and a new production code must be added to the manifest consciously."""
    _import_every_production_module()
    golden = json.loads(GOLDEN.read_text())
    production = {
        code: qualified_name(cls)
        for code, cls in errors._REGISTRY.items()
        if cls.__module__.startswith("trinetra.")
    }
    assert production == golden


def test_golden_manifest_is_well_formed():
    golden = json.loads(GOLDEN.read_text())
    assert list(golden) == sorted(golden)
    for code, dotted in golden.items():
        validate_error_code(code)
        assert dotted.startswith("trinetra.")


def test_importing_errors_pulls_in_no_third_party_or_layer_modules():
    code = (
        "import sys\n"
        "before=set(sys.modules)\n"
        "import trinetra.errors\n"
        "new={m.split('.')[0] for m in set(sys.modules)-before}\n"
        "layers=[m for m in sys.modules if m.startswith('trinetra.') "
        "and m not in ('trinetra.errors',)]\n"
        "print(sorted(new), layers)\n"
        "raise SystemExit(1 if layers else 0)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pydantic" not in result.stdout
