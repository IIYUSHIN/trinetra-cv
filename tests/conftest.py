"""Base fixtures and temp-state isolation for the whole test suite (IMP-0012).

Every test runs hermetically:

* no ``TRINETRA_*`` variable from the developer's shell leaks in, and the
  timezone is pinned to UTC;
* IP networking (incl. loopback and DNS) is blocked in-process unless the test
  is marked ``network`` AND ``TRINETRA_NETWORK_TESTS=1`` is set;
* each test gets its own private ``state_dir``; nothing may be written into
  the repository (checked once per session, content-hashed);
* Hypothesis runs with a fixed, local profile (no example database in the repo,
  no wall-clock deadlines).

``network``-marked tests are reported as skipped (NOT RUN - never PASS) unless
explicitly opted in.
"""

from __future__ import annotations

import io
import logging
import os
import socket
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

import repo_guard
from trinetra import errors
from trinetra.structured_log import configure_logging
from trinetra.timeutil import FixedClock, ManualClock

REPO_ROOT = Path(__file__).resolve().parents[1]
NETWORK_ENV = "TRINETRA_NETWORK_TESTS"
ENV_PREFIX = "TRINETRA_"
FIXED_MOMENT = datetime(2026, 9, 2, 14, 35, 20, tzinfo=UTC)
FIXED_CORRELATION_ID = "0123456789abcdef0123456789abcdef"

settings.register_profile(
    "trinetra",
    deadline=None,  # wall-clock deadlines make results machine-dependent
    database=None,  # never write .hypothesis/ examples into the repository
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("trinetra")


class NetworkBlockedError(RuntimeError):
    """Raised when a test touches IP networking without opting in."""


def network_opted_in(environ: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    return environ.get(NETWORK_ENV) == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if network_opted_in():
        return
    skip = pytest.mark.skip(reason=f"network test: set {NETWORK_ENV}=1 (NOT RUN, not PASS)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


def _blocked(*_args: object, **_kwargs: object) -> None:
    raise NetworkBlockedError(
        f"IP networking is blocked in tests; mark the test @pytest.mark.network and set {NETWORK_ENV}=1"
    )


@pytest.fixture(autouse=True)
def _hermetic_process(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in [n for n in os.environ if n.startswith(ENV_PREFIX)]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()

    if "network" not in request.keywords:
        real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
        ip_families = (socket.AF_INET, socket.AF_INET6)

        def guarded_connect(self: socket.socket, address: object) -> None:
            if self.family in ip_families:
                _blocked()
            return real_connect(self, address)  # type: ignore[arg-type]

        def guarded_connect_ex(self: socket.socket, address: object) -> int:
            if self.family in ip_families:
                _blocked()
            return real_connect_ex(self, address)  # type: ignore[arg-type]

        monkeypatch.setattr(socket.socket, "connect", guarded_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        for attr in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
            monkeypatch.setattr(socket, attr, _blocked)
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(scope="session", autouse=True)
def _repository_is_left_untouched() -> Iterator[None]:
    root = repo_guard.git_root(REPO_ROOT)
    if root is None:  # source archive without git: nothing to compare against
        yield
        return
    before = repo_guard.snapshot(root)
    yield
    changes = repo_guard.diff(before, repo_guard.snapshot(root))
    if changes:
        pytest.fail("tests modified the repository:\n  " + "\n  ".join(changes), pytrace=False)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """The only place a test may keep state: private (0700), per test, outside the repo."""
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Opt-in: point HOME and XDG_* at an empty directory (kept opt-in because tools such
    as uv derive their cache location from HOME)."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_DATA_HOME", ".local/share")):
        monkeypatch.setenv(var, str(home / sub))
    return home


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(FIXED_MOMENT, monotonic_ns=1_000)


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock(FIXED_MOMENT, monotonic_ns=1_000)


@pytest.fixture
def fixed_correlation_id() -> str:
    return FIXED_CORRELATION_ID


@pytest.fixture
def error_registry_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classes declared inside the test do not leak into the process-wide error registry."""
    monkeypatch.setattr(errors, "_REGISTRY", dict(errors._REGISTRY))


@pytest.fixture
def structured_log(fixed_clock: FixedClock) -> Iterator[Callable[[], list[str]]]:
    """A private structured logger; call the returned function to read emitted lines.

    The logger is available as ``logging.getLogger(structured_log.logger_name)``.
    """
    name = f"trinetra.test.{os.getpid()}.{id(fixed_clock)}"
    stream = io.StringIO()
    handler = configure_logging(level="DEBUG", stream=stream, logger_name=name, clock=fixed_clock)

    def lines() -> list[str]:
        return [line for line in stream.getvalue().split("\n") if line]

    lines.logger_name = name  # type: ignore[attr-defined]
    yield lines
    logger = logging.getLogger(name)
    logger.removeHandler(handler)
    handler.close()
