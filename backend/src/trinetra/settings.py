"""Strict configuration/settings loader (IMP-0006).

Standard library only; offline; no remote telemetry, no downloads, no `.env`
auto-discovery. The loader is *domain-neutral*: it defines no setting names of
its own. Packages declare a schema of `Setting` objects and call
`load_settings`.

Contract (PROPOSED engineering decisions; the Bible fixes only "strict
settings" in module I1 and the generic packet text):

* Precedence: schema default < TOML file < environment.
* Fail closed: unknown file keys, unknown prefixed environment variables,
  missing required values, wrong types, out-of-range values and malformed
  files all raise `ConfigError`. Nothing is silently ignored or coerced.
* TOML values are NOT coerced (``"1"`` is not an int; ``true`` is not an int).
  Environment values are strings and are parsed with strict grammars.
* ``secret=True`` settings may come only from the environment, never from a
  file (no secrets in repositories), and are redacted in ``repr``/``redacted``.
* The result is immutable and records the source of every value.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from trinetra.errors import TrinetraError

NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
ENV_PREFIX_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9]*_")
MAX_NAME_LENGTH: Final = 64
MAX_STRING_LENGTH: Final = 4096
MAX_FILE_BYTES: Final = 65_536
MAX_SAFE_INTEGER: Final = 2**53 - 1
REDACTED: Final = "***REDACTED***"
_INT_RE: Final = re.compile(r"[+-]?(?:0|[1-9][0-9]*)")
_FLOAT_RE: Final = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")

SettingValue = str | int | float | bool


class ConfigError(TrinetraError):
    code = "CONFIG_INVALID"
    default_message = "invalid configuration"


class ConfigFileError(TrinetraError):
    code = "CONFIG_FILE_UNREADABLE"
    default_message = "configuration file cannot be read"


class _Required:
    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED: Final = _Required()


def _fail(key: str, problem: str, **extra: str | int) -> ConfigError:
    return ConfigError(f"{key}: {problem}", details={"key": key, "problem": problem, **extra})


@dataclass(frozen=True)
class Setting:
    """One declared setting. Validated at construction (fail early)."""

    name: str
    kind: type
    default: Any = REQUIRED
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] | None = None
    secret: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or len(self.name) > MAX_NAME_LENGTH
            or not NAME_PATTERN.fullmatch(self.name)
        ):
            raise ValueError(f"invalid setting name {self.name!r}")
        if self.kind not in (str, int, float, bool):
            raise ValueError(f"{self.name}: kind must be str, int, float or bool")
        if (self.minimum is not None or self.maximum is not None) and self.kind not in (int, float):
            raise ValueError(f"{self.name}: bounds apply only to int/float")
        for bound in (self.minimum, self.maximum):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, int | float)
                or not math.isfinite(bound)
            ):
                raise ValueError(f"{self.name}: bounds must be finite numbers")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"{self.name}: minimum exceeds maximum")
        if self.choices is not None:
            if self.kind is not str or not self.choices or len(set(self.choices)) != len(self.choices):
                raise ValueError(f"{self.name}: choices need a str kind and unique values")
            if not all(isinstance(c, str) for c in self.choices):
                raise ValueError(f"{self.name}: choices must be strings")
        if self.secret and self.default is not REQUIRED:
            raise ValueError(f"{self.name}: a secret must not have a default in code")
        if self.default is not REQUIRED:
            try:
                self.check(self.default)
            except ConfigError as exc:
                raise ValueError(f"{self.name}: default is invalid ({exc.details['problem']})") from exc

    @property
    def env_suffix(self) -> str:
        return self.name.upper()

    def check(self, value: object) -> SettingValue:
        """Validate an already-typed value; never coerces."""
        key = self.name
        if self.kind is bool:
            if not isinstance(value, bool):
                raise _fail(key, "expected bool")
            return value
        if self.kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise _fail(key, "expected int")
            if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
                raise _fail(key, "integer outside I-JSON safe range")
        elif self.kind is float:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise _fail(key, "expected float")
            if isinstance(value, int) and not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
                raise _fail(key, "integer outside I-JSON safe range")
            value = float(value)
            if not math.isfinite(value):
                raise _fail(key, "float must be finite")
        else:
            if not isinstance(value, str):
                raise _fail(key, "expected str")
            if len(value) > MAX_STRING_LENGTH:
                raise _fail(key, f"string longer than {MAX_STRING_LENGTH}")
            if _CONTROL_RE.search(value):
                raise _fail(key, "control characters are not allowed")
            if self.choices is not None and value not in self.choices:
                raise _fail(key, "value not among the allowed choices")
            return value
        if self.minimum is not None and value < self.minimum:
            raise _fail(key, f"below minimum {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise _fail(key, f"above maximum {self.maximum}")
        return value

    def parse_env(self, text: str) -> SettingValue:
        """Parse an environment string with a strict grammar, then validate."""
        key = self.name
        if self.kind is bool:
            if text not in ("true", "false"):
                raise _fail(key, "environment bool must be exactly 'true' or 'false'")
            return self.check(text == "true")
        if self.kind is int:
            if not _INT_RE.fullmatch(text):
                raise _fail(key, "environment value is not a plain decimal integer")
            return self.check(int(text))
        if self.kind is float:
            if not _FLOAT_RE.fullmatch(text):
                raise _fail(key, "environment value is not a plain decimal number")
            return self.check(float(text))
        return self.check(text)


class Settings(Mapping[str, SettingValue]):
    """Immutable, attribute-readable result of `load_settings`."""

    __slots__ = ("_secret", "_sources", "_values")

    def __init__(
        self,
        values: Mapping[str, SettingValue],
        sources: Mapping[str, str],
        secret: frozenset[str],
    ) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))
        object.__setattr__(self, "_sources", MappingProxyType(dict(sources)))
        object.__setattr__(self, "_secret", secret)

    def __getitem__(self, key: str) -> SettingValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getattr__(self, name: str) -> SettingValue:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Settings is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Settings is immutable")

    @property
    def sources(self) -> Mapping[str, str]:
        """Where each value came from: 'default', 'file' or 'env'."""
        return self._sources

    def redacted(self) -> dict[str, SettingValue]:
        """Safe-to-log view: secrets replaced by a fixed marker."""
        return {k: (REDACTED if k in self._secret else v) for k, v in self._values.items()}

    def __repr__(self) -> str:
        return f"Settings({self.redacted()!r})"

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("Settings must not be pickled (may hold secrets)")


def _read_file(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise ConfigFileError("configuration file must not be a symlink",
                                  details={"path": str(path)})
        if not path.is_file():
            raise ConfigFileError("configuration file is not a regular file",
                                  details={"path": str(path)})
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigFileError(f"cannot read configuration file: {type(exc).__name__}",
                              details={"path": str(path)}) from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ConfigFileError(f"configuration file larger than {MAX_FILE_BYTES} bytes",
                              details={"path": str(path)})
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            f"configuration file is not valid UTF-8 TOML: {type(exc).__name__}",
            details={"key": "<file>", "problem": "malformed TOML"},
        ) from exc


def load_settings(
    schema: Sequence[Setting],
    *,
    file: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    env_prefix: str = "TRINETRA_",
    ignore_env: frozenset[str] = frozenset(),
) -> Settings:
    """Load and validate settings. Raises `ConfigError`/`ConfigFileError`.

    `env=None` reads the real process environment at call time. Environment
    variables that start with `env_prefix` but match no setting (and are not
    listed in `ignore_env`) are rejected, so typos never pass silently.
    """
    if not isinstance(env_prefix, str) or not ENV_PREFIX_PATTERN.fullmatch(env_prefix):
        raise ValueError("env_prefix must look like 'NAME_'")
    by_name: dict[str, Setting] = {}
    for spec in schema:
        if not isinstance(spec, Setting):
            raise ValueError("schema entries must be Setting objects")  # noqa: TRY004
        if spec.name in by_name:
            raise ValueError(f"duplicate setting {spec.name!r}")
        by_name[spec.name] = spec
    env_names = {env_prefix + s.env_suffix: s for s in by_name.values()}
    if len(env_names) != len(by_name):
        raise ValueError("settings collide on their environment variable names")

    values: dict[str, SettingValue] = {}
    sources: dict[str, str] = {}

    if file is not None:
        table = _read_file(Path(file))
        for key, raw in table.items():
            spec = by_name.get(key)
            if spec is None:
                raise _fail(str(key), "unknown setting in file")
            if spec.secret:
                raise _fail(key, "secret settings must come from the environment, not a file")
            values[key] = spec.check(raw)
            sources[key] = "file"

    environment = os.environ if env is None else env
    for var, text in environment.items():
        if not var.startswith(env_prefix) or var in ignore_env:
            continue
        spec = env_names.get(var)
        if spec is None:
            raise _fail(var, "unknown prefixed environment variable")
        if not isinstance(text, str):
            raise _fail(spec.name, "environment value must be str")
        values[spec.name] = spec.parse_env(text)
        sources[spec.name] = "env"

    for name, spec in by_name.items():
        if name in values:
            continue
        if spec.default is REQUIRED:
            raise _fail(name, "required setting is missing")
        values[name] = spec.check(spec.default)
        sources[name] = "default"

    ordered = {name: values[name] for name in sorted(by_name)}
    return Settings(ordered, {k: sources[k] for k in ordered},
                    frozenset(n for n, s in by_name.items() if s.secret))
