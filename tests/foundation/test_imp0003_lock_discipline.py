"""IMP-0003 tests: uv lock discipline and offline-compatible dependency policy.

Structure:
  * `check_lock` is the executable lock policy (pure, offline, deterministic).
  * mutation tests prove each rule can fail;
  * opt-in tests (TRINETRA_NETWORK_TESTS=1) exercise real `uv` behaviour that
    needs a package index once: `uv lock --check`, and a warm-cache -> fully
    offline install proof. They are reported as skipped (NOT RUN) otherwise.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from test_imp0002_project_metadata import FORBIDDEN, PYPROJECT, REPO_ROOT, load

LOCKFILE = REPO_ROOT / "uv.lock"
ROOT_SOURCE = {"editable": "."}
ALLOWED_REGISTRIES = {"https://pypi.org/simple"}
ALLOWED_ARTIFACT_HOSTS = ("https://files.pythonhosted.org/",)
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CUDA_PREFIXES = ("nvidia-", "cuda-", "triton")
# Explicit, reviewed transitive exceptions to the "no telemetry dependency"
# rule: name -> the only packages allowed to pull it in. opentelemetry-api is the
# no-op API package (no SDK, no exporter, no network); every FastAPI >= 0.150
# requires it. The SDK and all exporters stay forbidden. OPEN DECISION for
# Father/D40: confirm or replace this exception.
TRANSITIVE_ALLOWED = {"opentelemetry-api": {"fastapi"}}
NETWORK = os.environ.get("TRINETRA_NETWORK_TESTS") == "1"
UV = shutil.which("uv")


def load_lock(path: Path = LOCKFILE) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _direct_requirements(pyproject: dict):
    project = pyproject["project"]
    yield from project.get("dependencies", [])
    for items in project.get("optional-dependencies", {}).values():
        yield from items
    for items in pyproject.get("dependency-groups", {}).values():
        for raw in items:
            if isinstance(raw, str):
                yield raw


def dependents_of(lock: dict, target: str) -> set[str]:
    found = set()
    for pkg in lock["package"]:
        edges = list(pkg.get("dependencies", []))
        for group in pkg.get("optional-dependencies", {}).values():
            edges += group
        if any(canonicalize_name(e["name"]) == target for e in edges):
            found.add(canonicalize_name(pkg["name"]))
    return found


def check_lock(lock: dict, pyproject: dict) -> list[str]:
    """Return lock-policy violations; empty means conformant."""
    problems: list[str] = []
    if lock.get("version") != 1:
        problems.append("unsupported uv.lock schema version")
    if lock.get("requires-python") != "==3.12.*":
        problems.append("lock requires-python must be ==3.12.*")
    uv_cfg = pyproject.get("tool", {}).get("uv", {})
    pinned_date = uv_cfg.get("exclude-newer")
    if not pinned_date:
        problems.append("pyproject must set tool.uv.exclude-newer for reproducible re-resolution")
    elif lock.get("options", {}).get("exclude-newer") != pinned_date:
        problems.append("lock exclude-newer differs from pyproject (stale or hand-edited lock)")

    packages = lock.get("package", [])
    dependents: dict[str, set[str]] = {}
    for pkg in packages:
        edges = list(pkg.get("dependencies", []))
        for group in pkg.get("optional-dependencies", {}).values():
            edges += group
        for edge in edges:
            dependents.setdefault(canonicalize_name(edge["name"]), set()).add(
                canonicalize_name(pkg["name"]))
    locked: dict[str, list[str]] = {}
    roots = 0
    for pkg in packages:
        name = canonicalize_name(pkg.get("name", ""))
        source = pkg.get("source", {})
        if source == ROOT_SOURCE:
            roots += 1
            if name != "trinetra-cv":
                problems.append(f"root package must be trinetra-cv, got {name}")
            continue
        label = f"{name}=={pkg.get('version')}"
        if set(source) != {"registry"} or source.get("registry") not in ALLOWED_REGISTRIES:
            problems.append(f"{label}: unexplained dependency source {source}")
        if name.startswith("opentelemetry") and name not in TRANSITIVE_ALLOWED:
            problems.append(f"{label}: forbidden dependency (telemetry)")
        elif name in FORBIDDEN:
            allowed_by = TRANSITIVE_ALLOWED.get(name)
            if allowed_by is None or not dependents.get(name, set()) <= allowed_by:
                problems.append(f"{label}: forbidden dependency")
        if name.startswith(CUDA_PREFIXES):
            problems.append(f"{label}: CUDA/GPU package in default lock (CPU is mandatory)")
        locked.setdefault(name, []).append(pkg.get("version", ""))
        artifacts = list(pkg.get("wheels", []))
        if "sdist" in pkg:
            artifacts.append(pkg["sdist"])
        if not artifacts:
            problems.append(f"{label}: no artifacts")
        for art in artifacts:
            url = art.get("url", "")
            if not url.startswith(ALLOWED_ARTIFACT_HOSTS):
                problems.append(f"{label}: artifact outside allowed hosts: {url[:80]}")
            if not HASH_RE.match(art.get("hash", "")):
                problems.append(f"{label}: missing or malformed sha256 hash for {url[-40:]}")
    if roots != 1:
        problems.append(f"expected exactly one root package, found {roots}")
    for name, versions in locked.items():
        if len(versions) != len(set(versions)):
            problems.append(f"{name}: duplicate locked version")

    for raw in _direct_requirements(pyproject):
        req = Requirement(raw)
        name = canonicalize_name(req.name)
        if name not in locked:
            problems.append(f"direct requirement {raw!r} missing from lock")
        elif not any(req.specifier.contains(Version(v), prereleases=True) for v in locked[name]):
            problems.append(f"locked {name} {locked[name]} violates {req.specifier}")
    return problems


# ---------------------------------------------------------------- happy path
def test_lockfile_exists_and_is_trackable():
    assert LOCKFILE.is_file() and not LOCKFILE.is_symlink()
    git = shutil.which("git")
    if git is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout (NOT RUN)")
    for probe in ("uv.lock", "backend/uv.lock"):
        result = subprocess.run(
            [git, "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q", "--", probe],
            check=False,
        )
        assert result.returncode == 1, f"{probe} is ignored by git"


def test_real_lock_conforms_to_policy():
    assert check_lock(load_lock(), load()) == []


def test_lock_is_utf8_lf_with_final_newline():
    raw = LOCKFILE.read_bytes()
    raw.decode("utf-8")
    assert b"\r" not in raw and raw.endswith(b"\n") and not raw.startswith(b"\xef\xbb\xbf")


def test_lock_has_no_cuda_or_telemetry_packages():
    names = {canonicalize_name(p["name"]) for p in load_lock()["package"]}
    assert not {n for n in names if n.startswith(CUDA_PREFIXES)}
    assert names & FORBIDDEN == {"opentelemetry-api"}  # the one reviewed exception
    assert not {n for n in names if n.startswith("opentelemetry") and n != "opentelemetry-api"}
    assert dependents_of(load_lock(), "opentelemetry-api") == {"fastapi"}


def test_every_artifact_is_hash_pinned():
    count = 0
    for pkg in load_lock()["package"]:
        if pkg["source"] == ROOT_SOURCE:
            continue
        for art in [*pkg.get("wheels", []), *([pkg["sdist"]] if "sdist" in pkg else [])]:
            assert HASH_RE.match(art["hash"])
            count += 1
    assert count > 40


# ----------------------------------------------------------------- negative
def _mutated_lock(mutator) -> list[str]:
    lock = copy.deepcopy(load_lock())
    mutator(lock)
    return check_lock(lock, load())


def _pkg(lock: dict, name: str) -> dict:
    return next(p for p in lock["package"] if p["name"] == name)


LOCK_NEGATIVE_CASES = {
    "git_source": (
        lambda lk: _pkg(lk, "pydantic").__setitem__(
            "source", {"git": "https://example.invalid/pydantic.git"}),
        "unexplained dependency source",
    ),
    "foreign_registry": (
        lambda lk: _pkg(lk, "pydantic").__setitem__(
            "source", {"registry": "https://example.invalid/simple"}),
        "unexplained dependency source",
    ),
    "path_source": (
        lambda lk: _pkg(lk, "pydantic").__setitem__("source", {"path": "../pydantic"}),
        "unexplained dependency source",
    ),
    "missing_hash": (
        lambda lk: _pkg(lk, "pydantic")["wheels"][0].pop("hash"),
        "missing or malformed sha256",
    ),
    "weak_hash": (
        lambda lk: _pkg(lk, "pydantic")["wheels"][0].__setitem__("hash", "md5:" + "0" * 32),
        "missing or malformed sha256",
    ),
    "short_hash": (
        lambda lk: _pkg(lk, "pydantic")["wheels"][0].__setitem__("hash", "sha256:abc"),
        "missing or malformed sha256",
    ),
    "artifact_from_foreign_host": (
        lambda lk: _pkg(lk, "pydantic")["wheels"][0].__setitem__(
            "url", "https://evil.invalid/pydantic.whl"),
        "artifact outside allowed hosts",
    ),
    "plain_http_artifact": (
        lambda lk: _pkg(lk, "pydantic")["wheels"][0].__setitem__(
            "url", "http://files.pythonhosted.org/x.whl"),
        "artifact outside allowed hosts",
    ),
    "cuda_package": (
        lambda lk: lk["package"].append({
            "name": "nvidia-cudnn-cu13", "version": "1.0.0",
            "source": {"registry": "https://pypi.org/simple"},
            "wheels": [{"url": "https://files.pythonhosted.org/a.whl",
                        "hash": "sha256:" + "a" * 64}],
        }),
        "CUDA/GPU package",
    ),
    "forbidden_package": (
        lambda lk: lk["package"].append({
            "name": "sentry-sdk", "version": "2.0.0",
            "source": {"registry": "https://pypi.org/simple"},
            "wheels": [{"url": "https://files.pythonhosted.org/a.whl",
                        "hash": "sha256:" + "b" * 64}],
        }),
        "forbidden dependency",
    ),
    "telemetry_sdk_transitive": (
        lambda lk: lk["package"].append({
            "name": "opentelemetry-sdk", "version": "1.45.2",
            "source": {"registry": "https://pypi.org/simple"},
            "wheels": [{"url": "https://files.pythonhosted.org/a.whl",
                        "hash": "sha256:" + "c" * 64}],
        }),
        "forbidden dependency (telemetry)",
    ),
    "telemetry_api_pulled_by_other_package": (
        lambda lk: _pkg(lk, "pydantic").__setitem__(
            "dependencies", [*_pkg(lk, "pydantic").get("dependencies", []),
                             {"name": "opentelemetry-api"}]),
        "opentelemetry-api==1.45.2: forbidden dependency",
    ),
    "package_without_artifacts": (
        lambda lk: (_pkg(lk, "pydantic").pop("wheels"), _pkg(lk, "pydantic").pop("sdist", None)),
        "no artifacts",
    ),
    "direct_dependency_missing": (
        lambda lk: lk["package"].remove(_pkg(lk, "pydantic")),
        "missing from lock",
    ),
    "locked_version_out_of_range": (
        lambda lk: _pkg(lk, "pydantic").__setitem__("version", "1.10.0"),
        "violates",
    ),
    "wrong_lock_python": (
        lambda lk: lk.__setitem__("requires-python", ">=3.12"),
        "lock requires-python must be",
    ),
    "stale_exclude_newer": (
        lambda lk: lk["options"].__setitem__("exclude-newer", "2020-01-01T00:00:00Z"),
        "exclude-newer differs",
    ),
    "unsupported_schema": (
        lambda lk: lk.__setitem__("version", 99),
        "schema version",
    ),
    "second_root": (
        lambda lk: lk["package"].append(
            {"name": "other", "version": "0", "source": ROOT_SOURCE}),
        "exactly one root package",
    ),
    "duplicate_locked_version": (
        lambda lk: lk["package"].append(copy.deepcopy(_pkg(lk, "pydantic"))),
        "duplicate locked version",
    ),
}


@pytest.mark.parametrize("case", sorted(LOCK_NEGATIVE_CASES))
def test_lock_policy_rejects_mutation(case):
    mutator, expected = LOCK_NEGATIVE_CASES[case]
    problems = _mutated_lock(mutator)
    assert any(expected in p for p in problems), (case, problems)


def test_pyproject_without_exclude_newer_is_rejected():
    pyproject = copy.deepcopy(load())
    del pyproject["tool"]["uv"]["exclude-newer"]
    assert any("exclude-newer" in p for p in check_lock(load_lock(), pyproject))


def test_new_direct_dependency_without_relock_is_rejected():
    pyproject = copy.deepcopy(load())
    pyproject["project"]["dependencies"].append("rich>=13,<15")
    problems = check_lock(load_lock(), pyproject)
    assert any("rich" in p and "missing from lock" in p for p in problems)


def test_malformed_lock_toml_is_rejected(tmp_path):
    bad = tmp_path / "uv.lock"
    bad.write_text("version = 1\n[[package\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_lock(bad)


# ------------------------------------------------- opt-in real uv / network
needs_network = pytest.mark.skipif(
    not NETWORK or UV is None,
    reason="set TRINETRA_NETWORK_TESTS=1 with uv installed (NOT RUN, not PASS)",
)


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=False, **kw)


@pytest.mark.network
@needs_network
def test_uv_lock_check_passes_for_committed_lock(tmp_path):
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    result = _run([UV, "lock", "--check", "--python", "3.12"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "uv.lock").read_bytes() == LOCKFILE.read_bytes()


@pytest.mark.network
@needs_network
def test_uv_lock_check_fails_when_pyproject_changes_without_relock(tmp_path):
    shutil.copy(REPO_ROOT / "uv.lock", tmp_path / "uv.lock")
    text = PYPROJECT.read_text().replace('"pydantic>=2.9,<3"', '"pydantic>=2.10,<3"')
    assert text != PYPROJECT.read_text()
    (tmp_path / "pyproject.toml").write_text(text)
    result = _run([UV, "lock", "--check", "--python", "3.12"], cwd=tmp_path)
    assert result.returncode != 0
    assert "needs to be updated" in result.stderr or "lockfile" in result.stderr.lower()


@pytest.mark.network
@needs_network
def test_frozen_install_works_fully_offline_from_warm_cache(tmp_path):
    """Physical offline-compatibility proof for the dev group.

    1. warm an isolated cache from the lock (the only step that touches the net);
    2. install into a NEW venv with --offline --frozen: must succeed;
    3. same install with an EMPTY cache and --offline: must fail, not hang or
       fall back to the network.
    """
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    shutil.copytree(REPO_ROOT / "backend" / "src", tmp_path / "backend" / "src",
                    ignore=shutil.ignore_patterns("__pycache__"))
    cache = tmp_path / "cache"
    base = [UV, "sync", "--frozen", "--group", "dev", "--python", "3.12"]
    env = {**os.environ, "UV_CACHE_DIR": str(cache),
           "UV_PROJECT_ENVIRONMENT": str(tmp_path / "v1")}
    warm = _run(base, cwd=tmp_path, env=env)
    assert warm.returncode == 0, warm.stderr

    env2 = {**env, "UV_PROJECT_ENVIRONMENT": str(tmp_path / "v2")}
    offline = _run([*base, "--offline"], cwd=tmp_path, env=env2)
    assert offline.returncode == 0, offline.stderr
    probe = _run([str(tmp_path / "v2" / "bin" / "python"), "-c",
                  "import pytest, hypothesis, pydantic, trinetra"])
    assert probe.returncode == 0, probe.stderr

    env3 = {**env, "UV_CACHE_DIR": str(tmp_path / "empty"),
            "UV_PROJECT_ENVIRONMENT": str(tmp_path / "v3")}
    cold = _run([*base, "--offline"], cwd=tmp_path, env=env3)
    assert cold.returncode != 0
    assert not (tmp_path / "v3" / "bin" / "pytest").exists()
