"""IMP-0012 tests: base fixtures and temp-state isolation.

The guards are proven both in-process (the fixtures are active for these very
tests) and in inner pytest runs against a private project holding a copy of
``tests/conftest.py`` - so we show the guard *fails* when it should, not just
that it exists.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from hypothesis import settings

import repo_guard
from conftest import FIXED_CORRELATION_ID, FIXED_MOMENT, NetworkBlockedError, network_opted_in
from trinetra.errors import TrinetraError, registered_error_codes
from trinetra.structured_log import log_event

TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_DIR.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git not installed (NOT RUN, not PASS)")


# --------------------------------------------------------- in-process guards
def test_trinetra_environment_is_scrubbed_for_every_test():
    assert [n for n in os.environ if n.startswith("TRINETRA_")] == []


def test_variables_set_inside_a_test_do_not_leak_to_the_next(monkeypatch):
    monkeypatch.setenv("TRINETRA_LEAK_PROBE", "1")
    assert os.environ["TRINETRA_LEAK_PROBE"] == "1"


def test_previous_test_leaked_nothing():
    assert "TRINETRA_LEAK_PROBE" not in os.environ


def test_timezone_is_pinned_to_utc():
    assert os.environ["TZ"] == "UTC"
    assert time.tzname == ("UTC", "UTC") and time.localtime(0).tm_hour == 0


def test_loopback_and_dns_are_blocked():
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("localhost", 80)
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyname("localhost")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(NetworkBlockedError):
            sock.connect_ex(("127.0.0.1", 9))


def _ipv6_available() -> bool:
    try:
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_available(), reason="IPv6 sockets unavailable here (NOT RUN, not PASS)")
def test_ipv6_connections_are_blocked_too():
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock6, pytest.raises(NetworkBlockedError):
        sock6.connect(("::1", 9, 0, 0))


def test_non_ip_sockets_still_work():
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock, pytest.raises(FileNotFoundError):
        sock.connect("/nonexistent/path.sock")  # reaches the real connect, which fails normally


def test_asyncio_streams_over_a_socketpair_still_work():
    async def exchange() -> bytes:
        left, right = socket.socketpair()
        _, writer = await asyncio.open_connection(sock=left)
        reader, other_writer = await asyncio.open_connection(sock=right)
        writer.write(b"x")
        await writer.drain()
        data = await reader.readexactly(1)
        for w in (writer, other_writer):
            w.close()
            await w.wait_closed()
        return data

    assert asyncio.run(exchange()) == b"x"


def test_state_dir_is_private_empty_and_outside_the_repository(state_dir, tmp_path):
    assert state_dir.parent == tmp_path and list(state_dir.iterdir()) == []
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert REPO_ROOT not in state_dir.parents
    (state_dir / "scratch.bin").write_bytes(b"data")


def test_isolated_home_redirects_home_and_xdg(isolated_home):
    assert Path.home() == isolated_home and isolated_home.is_dir()
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        assert Path(os.environ[var]).is_relative_to(isolated_home)


def test_clock_fixtures(fixed_clock, manual_clock, fixed_correlation_id):
    assert fixed_clock.now() == manual_clock.now() == FIXED_MOMENT
    assert fixed_clock.monotonic_ns() == manual_clock.monotonic_ns() == 1_000
    assert fixed_correlation_id == FIXED_CORRELATION_ID and len(fixed_correlation_id) == 32


def test_manual_clock_changes_do_not_survive_the_test(manual_clock):
    from datetime import timedelta

    manual_clock.advance(timedelta(hours=1))
    assert manual_clock.monotonic_ns() > 1_000


def test_manual_clock_is_fresh_in_the_next_test(manual_clock):
    assert manual_clock.now() == FIXED_MOMENT and manual_clock.monotonic_ns() == 1_000


def test_error_registry_isolation_part_one(error_registry_isolation):
    class LeakProbe(TrinetraError):
        code = "FIXTURE_LEAK_PROBE"

    assert "FIXTURE_LEAK_PROBE" in registered_error_codes()


def test_error_registry_isolation_part_two(error_registry_isolation):
    assert "FIXTURE_LEAK_PROBE" not in registered_error_codes()


def test_structured_log_fixture_is_private_and_deterministic(structured_log):
    log_event(logging.getLogger(structured_log.logger_name), "INFO", "fixture.check", n=1)
    [line] = structured_log()
    assert '"ts":"2026-09-02T14:35:20.000000Z"' in line and '"n":1' in line


def test_hypothesis_profile_is_deterministic_and_leaves_no_files():
    current = settings()
    assert current.deadline is None and current.database is None


def test_network_opt_in_flag_is_off_in_a_default_run_or_explicit():
    assert network_opted_in({"TRINETRA_NETWORK_TESTS": "1"}) is True
    for value in ("0", "", "true", "yes", "2", " 1"):
        assert network_opted_in({"TRINETRA_NETWORK_TESTS": value}) is False
    assert network_opted_in({}) is False


# ------------------------------------------------------- repo_guard (unit)
def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored/\n")
    (root / "tracked.txt").write_text("v1\n")
    (root / "other.txt").write_text("o\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


@needs_git
def test_guard_sees_a_clean_repo_as_clean(repo):
    assert repo_guard.snapshot(repo) == {}
    assert repo_guard.diff({}, {}) == []


@needs_git
def test_guard_detects_new_modified_and_deleted_files(repo):
    before = repo_guard.snapshot(repo)
    (repo / "new file with spaces é.txt").write_text("x")
    (repo / "tracked.txt").write_text("v2\n")
    (repo / "other.txt").unlink()
    problems = repo_guard.diff(before, repo_guard.snapshot(repo))
    joined = "\n".join(problems)
    assert "new file with spaces é.txt" in joined and "tracked.txt" in joined and "other.txt" in joined
    assert len(problems) == 3


@needs_git
def test_guard_detects_edits_to_an_already_dirty_file(repo):
    (repo / "tracked.txt").write_text("dirty-1\n")
    before = repo_guard.snapshot(repo)
    assert before and repo_guard.diff(before, repo_guard.snapshot(repo)) == []
    (repo / "tracked.txt").write_text("dirty-2\n")  # same git status code, different content
    assert [p.split(":")[0] for p in repo_guard.diff(before, repo_guard.snapshot(repo))] == ["tracked.txt"]


@needs_git
def test_guard_ignores_gitignored_files_and_handles_renames(repo):
    before = repo_guard.snapshot(repo)
    (repo / "ignored").mkdir()
    (repo / "ignored" / "cache.bin").write_bytes(b"c")
    assert repo_guard.diff(before, repo_guard.snapshot(repo)) == []
    git(repo, "mv", "tracked.txt", "renamed.txt")
    problems = repo_guard.diff(before, repo_guard.snapshot(repo))
    assert any("renamed.txt" in p for p in problems)


@needs_git
def test_guard_handles_odd_filenames(repo):
    before = repo_guard.snapshot(repo)
    (repo / "new\nline.txt").write_text("x")
    (repo / "-dash.txt").write_text("x")
    assert len(repo_guard.diff(before, repo_guard.snapshot(repo))) == 2


@needs_git
def test_git_root_detection(repo, tmp_path):
    assert repo_guard.git_root(repo) == repo
    (repo / "sub").mkdir()
    assert repo_guard.git_root(repo / "sub") == repo
    outside = tmp_path / "plain"
    outside.mkdir()
    assert repo_guard.git_root(outside) is None


def test_git_root_is_none_when_git_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert repo_guard.git_root(tmp_path) is None


# ----------------------------------------------- inner pytest (guards live)
def make_project(tmp_path: Path, tests: dict[str, str], *, git_init: bool = True) -> Path:
    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    shutil.copy(TESTS_DIR / "conftest.py", proj / "tests" / "conftest.py")
    shutil.copy(TESTS_DIR / "repo_guard.py", proj / "tests" / "repo_guard.py")
    for name, body in tests.items():
        (proj / "tests" / name).write_text(textwrap.dedent(body))
    (proj / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    if git_init:
        git(proj, "init", "-q")
        git(proj, "add", "-A")
        git(proj, "commit", "-q", "-m", "init")
    return proj


def run_inner(proj: Path, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TRINETRA_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(PYPROJECT), "--rootdir", str(proj),
         str(proj / "tests"), "-q"],
        cwd=proj, env={**clean, **env}, capture_output=True, text=True, check=False, timeout=180)


@needs_git
def test_inner_run_fails_when_a_test_writes_into_the_repository(tmp_path):
    proj = make_project(tmp_path, {"test_dirty.py": """
        from pathlib import Path

        def test_pollutes():
            (Path(__file__).resolve().parents[1] / "polluted.txt").write_text("oops")
    """})
    result = run_inner(proj)
    assert result.returncode != 0 and "tests modified the repository" in result.stdout
    assert "polluted.txt" in result.stdout


@needs_git
def test_inner_run_passes_when_tests_only_use_state_dir(tmp_path):
    proj = make_project(tmp_path, {"test_clean.py": """
        def test_uses_state_dir(state_dir):
            (state_dir / "x").write_text("fine")
    """})
    result = run_inner(proj)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inner_run_without_git_does_not_crash_the_guard(tmp_path):
    proj = make_project(tmp_path, {"test_clean.py": "def test_ok():\n    assert True\n"}, git_init=False)
    result = run_inner(proj)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inner_run_scrubs_the_outer_environment_and_timezone(tmp_path):
    proj = make_project(tmp_path, {"test_env.py": """
        import os
        import time

        def test_scrubbed():
            assert not [n for n in os.environ if n.startswith("TRINETRA_")]
            assert time.tzname == ("UTC", "UTC")
    """}, git_init=False)
    result = run_inner(proj, TRINETRA_FROM_SHELL="1", TZ="America/New_York")
    assert result.returncode == 0, result.stdout + result.stderr


LOOPBACK_TEST = """
    import socket

    import pytest

    def talk():
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname())
        conn, _ = server.accept()
        client.sendall(b"hi")
        assert conn.recv(2) == b"hi"
        for s in (client, conn, server):
            s.close()

    @pytest.mark.network
    def test_marked():
        talk()

    def test_unmarked():
        talk()
"""


def test_inner_network_marker_is_skipped_by_default_and_unmarked_is_blocked(tmp_path):
    proj = make_project(tmp_path, {"test_net.py": LOOPBACK_TEST}, git_init=False)
    result = run_inner(proj)
    assert result.returncode == 1
    assert "1 failed" in result.stdout and "1 skipped" in result.stdout
    assert "NetworkBlockedError" in result.stdout and "NOT RUN" in result.stdout


def test_inner_network_marker_runs_only_with_explicit_opt_in(tmp_path):
    proj = make_project(tmp_path, {"test_net.py": LOOPBACK_TEST}, git_init=False)
    result = run_inner(proj, TRINETRA_NETWORK_TESTS="1")
    assert "1 passed" in result.stdout  # the marked test really connected
    assert "1 failed" in result.stdout and "NetworkBlockedError" in result.stdout  # unmarked still blocked
