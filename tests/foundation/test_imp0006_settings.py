"""IMP-0006 tests: strict configuration/settings loader."""

from __future__ import annotations

import copy
import json
import os
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trinetra.errors import TrinetraError
from trinetra.settings import (
    MAX_FILE_BYTES,
    MAX_SAFE_INTEGER,
    MAX_STRING_LENGTH,
    REDACTED,
    REQUIRED,
    ConfigError,
    ConfigFileError,
    Setting,
    load_settings,
)

SCHEMA = [
    Setting("workers", int, default=2, minimum=1, maximum=64),
    Setting("ratio", float, default=0.5, minimum=0.0, maximum=1.0),
    Setting("verbose", bool, default=False),
    Setting("mode", str, default="strict", choices=("strict", "relaxed")),
    Setting("label", str, default=""),
    Setting("api_key", str, secret=True),
]
KEY = {"TRINETRA_API_KEY": "s3cret"}


def write(tmp_path, text, name="cfg.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def problem(exc_info):
    return exc_info.value.details["problem"]


# ---------------------------------------------------------------- happy path
def test_defaults_file_env_precedence_and_sources(tmp_path):
    cfg = write(tmp_path, 'workers = 4\nratio = 0.25\nlabel = "from-file"\n')
    env = {**KEY, "TRINETRA_WORKERS": "8", "UNRELATED": "x"}
    s = load_settings(SCHEMA, file=cfg, env=env)
    assert (s.workers, s.ratio, s.label, s.mode, s.verbose) == (8, 0.25, "from-file", "strict", False)
    assert s.sources == {
        "api_key": "env", "label": "file", "mode": "default", "ratio": "file",
        "verbose": "default", "workers": "env",
    }
    assert s["workers"] == 8 and len(s) == 6 and list(s) == sorted(s)


def test_loads_with_no_file_and_only_required_env():
    s = load_settings(SCHEMA, env=KEY)
    assert s.workers == 2 and s.sources["workers"] == "default"


def test_real_process_environment_is_read_when_env_is_none(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TRINETRA_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("TRINETRA_API_KEY", "k")
    monkeypatch.setenv("TRINETRA_VERBOSE", "true")
    s = load_settings(SCHEMA)
    assert s.verbose is True and s.api_key == "k"


def test_ignore_env_lets_known_foreign_variables_through():
    s = load_settings(SCHEMA, env={**KEY, "TRINETRA_NETWORK_TESTS": "1"},
                      ignore_env=frozenset({"TRINETRA_NETWORK_TESTS"}))
    assert "network_tests" not in s


def test_int_value_is_accepted_for_float_setting(tmp_path):
    assert load_settings(SCHEMA, file=write(tmp_path, "ratio = 1\n"), env=KEY).ratio == 1.0


def test_custom_env_prefix():
    s = load_settings(SCHEMA, env={"APP_API_KEY": "k", "TRINETRA_WORKERS": "9"}, env_prefix="APP_")
    assert s.workers == 2  # TRINETRA_* is not this app's namespace


def test_settings_are_immutable_and_attribute_safe():
    s = load_settings(SCHEMA, env=KEY)
    for action in (lambda: setattr(s, "workers", 9), lambda: delattr(s, "workers"),
                   lambda: s._values.__setitem__("workers", 9)):  # type: ignore[index]
        with pytest.raises((AttributeError, TypeError)):
            action()
    with pytest.raises(AttributeError):
        _ = s.nonexistent


# ------------------------------------------------------------ secrets
def test_secret_is_redacted_in_repr_and_redacted_view():
    s = load_settings(SCHEMA, env=KEY)
    assert "s3cret" not in repr(s) and REDACTED in repr(s)
    assert s.redacted()["api_key"] == REDACTED and s.redacted()["workers"] == 2
    assert s.api_key == "s3cret"


def test_secret_from_file_is_refused(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_settings(SCHEMA, file=write(tmp_path, 'api_key = "leaked"\n'), env=KEY)
    assert "environment" in problem(info) and "leaked" not in str(info.value)


def test_secret_may_not_have_a_default():
    with pytest.raises(ValueError, match="secret"):
        Setting("token", str, default="hunter2", secret=True)


def test_missing_secret_is_an_error_not_a_default():
    with pytest.raises(ConfigError) as info:
        load_settings(SCHEMA, env={})
    assert info.value.details["key"] == "api_key"


def test_settings_cannot_be_pickled_or_deep_copied_into_logs():
    s = load_settings(SCHEMA, env=KEY)
    with pytest.raises(TypeError):
        pickle.dumps(s)
    with pytest.raises(TypeError):
        copy.deepcopy(s)


def test_invalid_value_error_never_echoes_the_value(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_settings(SCHEMA, env={**KEY, "TRINETRA_WORKERS": "hunter2hunter2"})
    assert "hunter2" not in str(info.value) and "hunter2" not in json.dumps(info.value.to_dict())


# ------------------------------------------------------------------ strictness
def test_unknown_file_key_rejected(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_settings(SCHEMA, file=write(tmp_path, "wrkers = 3\n"), env=KEY)
    assert info.value.details["key"] == "wrkers" and "unknown" in problem(info)


def test_unknown_prefixed_env_var_rejected():
    with pytest.raises(ConfigError) as info:
        load_settings(SCHEMA, env={**KEY, "TRINETRA_WORKRES": "3"})
    assert "unknown prefixed" in problem(info)


def test_nested_table_in_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(SCHEMA, file=write(tmp_path, "[workers]\nx = 1\n"), env=KEY)


def test_missing_required_setting():
    with pytest.raises(ConfigError, match="required"):
        load_settings([Setting("needed", int)], env={})


@pytest.mark.parametrize(
    "text",
    ['workers = "4"', "workers = 4.0", "workers = true", "verbose = 1", 'verbose = "true"',
     "ratio = true", 'ratio = "0.5"', "label = 5", "mode = 1", "workers = [1]",
     "workers = 1979-05-27", "label = 1979-05-27T07:32:00Z"],
)
def test_toml_values_are_never_coerced(tmp_path, text):
    with pytest.raises(ConfigError, match="expected"):
        load_settings(SCHEMA, file=write(tmp_path, text + "\n"), env=KEY)


@pytest.mark.parametrize(
    ("var", "text"),
    [("WORKERS", ""), ("WORKERS", " 4"), ("WORKERS", "4 "), ("WORKERS", "0x10"), ("WORKERS", "1_0"),
     ("WORKERS", "04"), ("WORKERS", "4.0"), ("WORKERS", "１２"), ("WORKERS", "1e1"),
     ("VERBOSE", "True"), ("VERBOSE", "1"), ("VERBOSE", "yes"), ("VERBOSE", ""),
     ("RATIO", "nan"), ("RATIO", "inf"), ("RATIO", ".5"), ("RATIO", "0.5f"), ("RATIO", "0,5"),
     ("RATIO", "1e999"), ("MODE", "Strict"), ("MODE", "other")],
)
def test_env_grammar_is_strict(var, text):
    with pytest.raises(ConfigError):
        load_settings(SCHEMA, env={**KEY, f"TRINETRA_{var}": text})


# ------------------------------------------------------------------- boundary
@pytest.mark.parametrize(("value", "ok"), [(1, True), (0, False), (64, True), (65, False), (-1, False)])
def test_numeric_bounds_are_inclusive(value, ok):
    env = {**KEY, "TRINETRA_WORKERS": str(value)}
    if ok:
        assert load_settings(SCHEMA, env=env).workers == value
    else:
        with pytest.raises(ConfigError, match=r"minimum|maximum"):
            load_settings(SCHEMA, env=env)


def test_float_bounds_inclusive():
    for text, ok in (("0", True), ("1", True), ("1.0000001", False), ("-0.1", False)):
        env = {**KEY, "TRINETRA_RATIO": text}
        if ok:
            load_settings(SCHEMA, env=env)
        else:
            with pytest.raises(ConfigError):
                load_settings(SCHEMA, env=env)


def test_integer_safe_range_boundary(tmp_path):
    big = [Setting("n", int)]
    assert load_settings(big, env={"TRINETRA_N": str(MAX_SAFE_INTEGER)}).n == MAX_SAFE_INTEGER
    assert load_settings(big, env={"TRINETRA_N": str(-MAX_SAFE_INTEGER)}).n == -MAX_SAFE_INTEGER
    for bad in (MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1):
        with pytest.raises(ConfigError, match="I-JSON"):
            load_settings(big, env={"TRINETRA_N": str(bad)})
        with pytest.raises(ConfigError, match="I-JSON"):
            load_settings(big, file=write(tmp_path, f"n = {bad}\n"), env={})


def test_string_length_boundary():
    one = [Setting("s", str)]
    assert len(load_settings(one, env={"TRINETRA_S": "x" * MAX_STRING_LENGTH}).s) == MAX_STRING_LENGTH
    with pytest.raises(ConfigError, match="longer"):
        load_settings(one, env={"TRINETRA_S": "x" * (MAX_STRING_LENGTH + 1)})


@pytest.mark.parametrize("bad", ["a\x00b", "a\nb", "a\x1bb", "a\x7fb", "tab\there"])
def test_control_characters_rejected(bad):
    with pytest.raises(ConfigError, match="control"):
        load_settings([Setting("s", str)], env={"TRINETRA_S": bad})


def test_file_size_boundary(tmp_path):
    pad = "# " + "x" * 100 + "\n"
    exact = (b'label = "a"\n') + pad.encode() * ((MAX_FILE_BYTES - 12) // len(pad))
    exact += b"#" * (MAX_FILE_BYTES - len(exact) - 1) + b"\n"
    assert len(exact) == MAX_FILE_BYTES
    path = tmp_path / "exact.toml"
    path.write_bytes(exact)
    assert load_settings(SCHEMA, file=path, env=KEY).label == "a"
    path.write_bytes(exact + b"#")
    with pytest.raises(ConfigFileError, match="larger"):
        load_settings(SCHEMA, file=path, env=KEY)


# --------------------------------------------------------------- hostile file
def test_missing_file_is_a_file_error_not_a_silent_default(tmp_path):
    with pytest.raises(ConfigFileError):
        load_settings(SCHEMA, file=tmp_path / "nope.toml", env=KEY)


def test_directory_and_symlink_are_refused(tmp_path):
    real = write(tmp_path, "workers = 3\n")
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    with pytest.raises(ConfigFileError, match="symlink"):
        load_settings(SCHEMA, file=link, env=KEY)
    with pytest.raises(ConfigFileError, match="regular"):
        load_settings(SCHEMA, file=tmp_path, env=KEY)


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe\x00", b"workers = ", b"workers = 1\nworkers = 2\n", b"[a\nb=1", b'x = "unterminated',
     b"\xef\xbb\xbfworkers = 3\n"],
)
def test_malformed_files_are_config_errors(tmp_path, raw):
    path = tmp_path / "bad.toml"
    path.write_bytes(raw)
    with pytest.raises(ConfigError):
        load_settings(SCHEMA, file=path, env=KEY)


def test_file_error_hierarchy_and_codes():
    assert issubclass(ConfigError, TrinetraError) and issubclass(ConfigFileError, TrinetraError)
    assert ConfigError.code == "CONFIG_INVALID" and ConfigFileError.code == "CONFIG_FILE_UNREADABLE"
    assert ConfigError("x", details={"key": "k", "problem": "p"}).to_dict()["error_code"] == "CONFIG_INVALID"


# --------------------------------------------------------------- schema errors
@pytest.mark.parametrize("name", ["", "Workers", "1x", "x_", "_x", "a__b", "a-b", "a b", "é", "x" * 65])
def test_bad_setting_names_rejected(name):
    with pytest.raises(ValueError, match="name"):
        Setting(name, int)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": list}, {"kind": bytes}, {"kind": str, "minimum": 1}, {"kind": bool, "maximum": 1},
        {"kind": int, "minimum": 5, "maximum": 1}, {"kind": int, "minimum": float("nan")},
        {"kind": int, "maximum": float("inf")}, {"kind": int, "minimum": True},
        {"kind": int, "choices": ("a",)}, {"kind": str, "choices": ()},
        {"kind": str, "choices": ("a", "a")}, {"kind": str, "choices": (1,)},
        {"kind": int, "default": 0, "minimum": 1}, {"kind": int, "default": "3"},
        {"kind": int, "default": True}, {"kind": str, "default": "z", "choices": ("a",)},
        {"kind": float, "default": float("nan")},
    ],
)
def test_bad_schema_rejected_at_construction(kwargs):
    with pytest.raises(ValueError):
        Setting("s", **kwargs)


def test_duplicate_and_colliding_settings_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        load_settings([Setting("a", int, default=1), Setting("a", int, default=2)], env={})
    with pytest.raises(ValueError, match="Setting objects"):
        load_settings(["a"], env={})  # type: ignore[list-item]


@pytest.mark.parametrize("prefix", ["", "trinetra_", "TRINETRA", "A B_", "_X_", "1X_", "X__"])
def test_bad_env_prefix_rejected(prefix):
    with pytest.raises(ValueError, match="env_prefix"):
        load_settings(SCHEMA, env=KEY, env_prefix=prefix)


def test_required_sentinel_is_not_a_legal_value():
    assert repr(REQUIRED) == "REQUIRED"
    with pytest.raises(ConfigError):
        load_settings([Setting("a", str)], env={})


# ------------------------------------------------------------------ properties
@given(st.integers(-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER))
def test_property_env_int_round_trip(n):
    assert load_settings([Setting("n", int)], env={"TRINETRA_N": str(n)}).n == n


@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_property_env_float_round_trip(x):
    text = repr(x)
    try:
        got = load_settings([Setting("x", float)], env={"TRINETRA_X": text}).x
    except ConfigError:
        # repr may use forms outside the strict grammar (e.g. '1e-05' is fine, 'inf' is not generated)
        pytest.fail(f"strict grammar rejected repr(float) {text!r}")
    assert got == x


@given(st.text(max_size=40))
def test_property_arbitrary_env_text_never_crashes_untyped(text):
    """Any string either loads or raises ConfigError - never another exception."""
    for kind in (int, float, bool, str):
        try:
            load_settings([Setting("v", kind)], env={"TRINETRA_V": text})
        except ConfigError:
            pass
