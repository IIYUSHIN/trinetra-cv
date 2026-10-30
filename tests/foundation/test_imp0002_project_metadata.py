"""IMP-0002 tests: Python 3.12 project metadata and dependency groups.

The policy function `check_pyproject` is the executable form of the packet
contract. Negative tests mutate a copy of the real pyproject.toml and require
the policy to reject it for the specific expected reason, so a policy that
passes everything cannot make this suite green.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

REQUIRES_PYTHON = SpecifierSet(">=3.12,<3.13")
EXPECTED_EXTRAS = {"persistence", "api", "crypto", "imaging", "runner-ml"}
EXPECTED_GROUPS = {"test", "lint", "dev"}
# Never allowed anywhere: Bible 13846 (no cloud/k8s/LLM/blockchain/PWA/telemetry
# baseline) and 14 / I6 (no Ultralytics-only dependency).
FORBIDDEN = {
    "ultralytics", "sentry-sdk", "posthog", "mixpanel", "segment-analytics-python",
    "opentelemetry-sdk", "opentelemetry-api", "datadog", "newrelic",
    "openai", "anthropic", "langchain", "web3", "boto3", "kubernetes",
}
# Must not be reachable from the Core runtime `dependencies` list.
NOT_IN_CORE = {
    "fastapi", "uvicorn", "starlette", "torch", "torchvision", "onnxruntime",
    "onnx", "sqlalchemy", "alembic",
}
RUNNER_ONLY = {"torch", "torchvision", "onnxruntime"}


def load(path: Path = PYPROJECT) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _requirements(data: dict):
    """Yield (location, raw_string) for every requirement string in the file."""
    project = data.get("project", {})
    for raw in project.get("dependencies", []):
        yield "dependencies", raw
    for extra, items in project.get("optional-dependencies", {}).items():
        for raw in items:
            yield f"extra:{extra}", raw
    for group, items in data.get("dependency-groups", {}).items():
        for raw in items:
            if isinstance(raw, str):
                yield f"group:{group}", raw


def check_pyproject(data: dict) -> list[str]:
    """Return a list of policy violations; empty means conformant."""
    problems: list[str] = []
    project = data.get("project")
    if not isinstance(project, dict):
        return ["missing [project] table"]

    if project.get("name") != "trinetra-cv":
        problems.append("project.name must be 'trinetra-cv'")
    if "Private :: Do Not Upload" not in project.get("classifiers", []):
        problems.append("missing 'Private :: Do Not Upload' classifier")
    try:
        declared = SpecifierSet(project.get("requires-python", ""))
    except Exception:  # noqa: BLE001 - any parse failure is a violation
        declared = None
    if declared is None or declared != REQUIRES_PYTHON:
        problems.append(f"requires-python must be exactly {REQUIRES_PYTHON}")
    for dynamic in ("dependencies", "optional-dependencies", "requires-python"):
        if dynamic in project.get("dynamic", []):
            problems.append(f"{dynamic} must not be dynamic")

    seen_per_location: dict[str, set[str]] = {}
    core_names: set[str] = set()
    where: dict[str, set[str]] = {}
    for loc, raw in _requirements(data):
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            problems.append(f"{loc}: invalid PEP 508 requirement {raw!r}")
            continue
        name = canonicalize_name(req.name)
        where.setdefault(name, set()).add(loc)
        if req.url:
            problems.append(f"{loc}: direct reference not allowed: {raw!r}")
        ops = {s.operator for s in req.specifier}
        has_lower = bool(ops & {">=", ">", "==", "~=", "==="})
        has_upper = bool(ops & {"<", "<=", "==", "~=", "==="})
        if not (has_lower and has_upper):
            problems.append(f"{loc}: {raw!r} must have lower and upper bounds")
        if name in FORBIDDEN:
            problems.append(f"{loc}: forbidden dependency {name}")
        bucket = seen_per_location.setdefault(loc, set())
        if name in bucket:
            problems.append(f"{loc}: duplicate requirement {name}")
        bucket.add(name)
        if loc == "dependencies":
            core_names.add(name)

    for name in sorted(core_names & NOT_IN_CORE):
        problems.append(f"{name} must not be a Core runtime dependency")
    for name in RUNNER_ONLY:
        extra_locs = where.get(name, set())
        if extra_locs - {"extra:runner-ml"}:
            problems.append(f"{name} allowed only in extra runner-ml, found {sorted(extra_locs)}")

    extras = set(project.get("optional-dependencies", {}))
    if extras != EXPECTED_EXTRAS:
        problems.append(f"extras must be {sorted(EXPECTED_EXTRAS)}, got {sorted(extras)}")
    groups = set(data.get("dependency-groups", {}))
    if groups != EXPECTED_GROUPS:
        problems.append(
            f"dependency-groups must be {sorted(EXPECTED_GROUPS)}, got {sorted(groups)}"
        )
    dev = data.get("dependency-groups", {}).get("dev", [])
    included = {i.get("include-group") for i in dev if isinstance(i, dict)}
    if included != {"test", "lint"}:
        problems.append("group dev must include exactly test and lint")

    uv = data.get("tool", {}).get("uv", {})
    for heavy in ("torch", "torchvision"):
        if heavy in where and heavy not in (uv.get("sources") or {}):
            problems.append(
                f"{heavy} requires an explicit tool.uv.sources index (CPU-only wheels); "
                "PyPI's default Linux wheel pulls CUDA packages"
            )
    for name, source in (uv.get("sources") or {}).items():
        entries = source if isinstance(source, list) else [source]
        for entry in entries:
            if set(entry) & {"git", "url", "path", "workspace"}:
                problems.append(f"tool.uv.sources.{name}: only index sources allowed")
    build = data.get("build-system", {})
    if build.get("build-backend") != "uv_build":
        problems.append("build-system.build-backend must be uv_build")
    reqs = build.get("requires", [])
    if len(reqs) != 1 or not reqs[0].startswith("uv_build>=") or "<" not in reqs[0]:
        problems.append("build-system.requires must be one bounded uv_build range")
    if uv.get("package") is False:
        problems.append("tool.uv.package must not be false: backend package is built (IMP-0004)")
    backend = uv.get("build-backend", {})
    if backend.get("module-root") != "backend/src" or backend.get("module-name") != "trinetra":
        problems.append("tool.uv.build-backend must map module trinetra at backend/src")
    return problems


# ---------------------------------------------------------------- happy path
def test_real_pyproject_conforms():
    assert check_pyproject(load()) == []


def test_running_interpreter_is_in_declared_range():
    version = Version(".".join(map(str, sys.version_info[:3])))
    assert REQUIRES_PYTHON.contains(version), (
        f"tests must run on Python 3.12.x, running {version}; use `uv run --python 3.12`"
    )


def test_every_requirement_parses_and_is_bounded():
    reqs = list(_requirements(load()))
    assert len(reqs) >= 15
    for _, raw in reqs:
        Requirement(raw)


def test_runner_ml_extra_holds_onnxruntime_and_defers_torch():
    data = load()
    raw = data["project"]["optional-dependencies"]["runner-ml"]
    runner = {canonicalize_name(Requirement(r).name) for r in raw}
    assert "onnxruntime" in runner
    # torch/torchvision are deferred until a CPU-only index is configured; the
    # policy refuses them without one (see torch_without_cpu_index below).
    assert not (runner & {"torch", "torchvision"})


def test_torch_with_cpu_index_source_is_accepted_by_policy():
    def add_torch(d):
        d["project"]["optional-dependencies"]["runner-ml"] += ["torch>=2.4,<3"]
        d["tool"]["uv"]["sources"] = {"torch": {"index": "pytorch-cpu"}}
    mutated = _mutated(add_torch)
    assert not any("torch" in p for p in mutated), mutated


def test_core_dependencies_exactly_pydantic():
    names = {canonicalize_name(Requirement(r).name) for r in load()["project"]["dependencies"]}
    assert names == {"pydantic"}


# ------------------------------------------------------------------ boundary
@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("3.11.9", False), ("3.12.0", True), ("3.12.3", True), ("3.12.99", True),
        ("3.13.0", False), ("3.13.0rc1", False), ("3.14.0", False), ("4.0.0", False),
    ],
)
def test_requires_python_boundaries(version, ok):
    declared = SpecifierSet(load()["project"]["requires-python"])
    assert declared.contains(version, prereleases=True) is ok


@given(major=st.integers(2, 4), minor=st.integers(0, 20), micro=st.integers(0, 30))
def test_requires_python_accepts_only_3_12_family(major, minor, micro):
    declared = SpecifierSet(load()["project"]["requires-python"])
    assert declared.contains(f"{major}.{minor}.{micro}") is ((major, minor) == (3, 12))


# ----------------------------------------------------------------- negative
def _mutated(mutator) -> list[str]:
    data = copy.deepcopy(load())
    mutator(data)
    return check_pyproject(data)


NEGATIVE_CASES = {
    "wrong_python_range": (
        lambda d: d["project"].__setitem__("requires-python", ">=3.11"),
        "requires-python must be exactly",
    ),
    "python_range_allows_313": (
        lambda d: d["project"].__setitem__("requires-python", ">=3.12,<3.14"),
        "requires-python must be exactly",
    ),
    "missing_python_range": (
        lambda d: d["project"].pop("requires-python"),
        "requires-python must be exactly",
    ),
    "unbounded_dependency": (
        lambda d: d["project"]["dependencies"].append("requests"),
        "must have lower and upper bounds",
    ),
    "lower_bound_only": (
        lambda d: d["project"]["dependencies"].__setitem__(0, "pydantic>=2.9"),
        "must have lower and upper bounds",
    ),
    "git_direct_reference": (
        lambda d: d["project"]["dependencies"].append("evil @ git+https://example.invalid/x.git"),
        "direct reference not allowed",
    ),
    "url_direct_reference": (
        lambda d: d["project"]["optional-dependencies"]["api"].append("pkg @ https://example.invalid/p.whl"),
        "direct reference not allowed",
    ),
    "invalid_requirement": (
        lambda d: d["project"]["dependencies"].append("not a valid ((req"),
        "invalid PEP 508 requirement",
    ),
    "telemetry_dependency": (
        lambda d: d["project"]["optional-dependencies"]["api"].append("sentry-sdk>=2,<3"),
        "forbidden dependency sentry-sdk",
    ),
    "ultralytics_dependency": (
        lambda d: d["project"]["optional-dependencies"]["runner-ml"].append("ultralytics>=8,<9"),
        "forbidden dependency ultralytics",
    ),
    "fastapi_in_core": (
        lambda d: d["project"]["dependencies"].append("fastapi>=0.115,<1"),
        "fastapi must not be a Core runtime dependency",
    ),
    "torch_in_core": (
        lambda d: d["project"]["dependencies"].append("torch>=2.4,<3"),
        "torch must not be a Core runtime dependency",
    ),
    "torch_outside_runner_extra": (
        lambda d: d["project"]["optional-dependencies"]["imaging"].append("torch>=2.4,<3"),
        "torch allowed only in extra runner-ml",
    ),
    "torch_without_cpu_index": (
        lambda d: d["project"]["optional-dependencies"]["runner-ml"].append("torch>=2.4,<3"),
        "torch requires an explicit tool.uv.sources index",
    ),
    "onnxruntime_in_dev_group": (
        lambda d: d["dependency-groups"]["test"].append("onnxruntime>=1.19,<2"),
        "onnxruntime allowed only in extra runner-ml",
    ),
    "duplicate_in_one_list": (
        lambda d: d["project"]["dependencies"].append("Pydantic>=2.9,<3"),
        "duplicate requirement pydantic",
    ),
    "extra_removed": (
        lambda d: d["project"]["optional-dependencies"].pop("crypto"),
        "extras must be",
    ),
    "group_added": (
        lambda d: d["dependency-groups"].__setitem__("misc", ["pytest>=8,<10"]),
        "dependency-groups must be",
    ),
    "dev_missing_lint": (
        lambda d: d["dependency-groups"].__setitem__("dev", [{"include-group": "test"}]),
        "group dev must include exactly test and lint",
    ),
    "uv_git_source": (
        lambda d: d.setdefault("tool", {}).setdefault("uv", {}).__setitem__(
            "sources", {"pydantic": {"git": "https://example.invalid/pydantic.git"}}),
        "only index sources allowed",
    ),
    "wrong_name": (
        lambda d: d["project"].__setitem__("name", "something-else"),
        "project.name must be",
    ),
    "uploadable_package": (
        lambda d: d["project"].__setitem__(
            "classifiers", ["Programming Language :: Python :: 3.12"]),
        "Do Not Upload",
    ),
    "dynamic_dependencies": (
        lambda d: d["project"].__setitem__("dynamic", ["dependencies"]),
        "must not be dynamic",
    ),
    "package_flag_back_to_virtual": (
        lambda d: d["tool"]["uv"].__setitem__("package", False),
        "tool.uv.package must not be false",
    ),
    "wrong_build_backend": (
        lambda d: d["build-system"].__setitem__("build-backend", "setuptools.build_meta"),
        "build-backend must be uv_build",
    ),
    "unbounded_build_requirement": (
        lambda d: d["build-system"].__setitem__("requires", ["uv_build"]),
        "one bounded uv_build range",
    ),
    "wrong_module_root": (
        lambda d: d["tool"]["uv"]["build-backend"].__setitem__("module-root", "src"),
        "must map module trinetra at backend/src",
    ),
    "missing_project_table": (
        lambda d: d.pop("project"),
        "missing [project] table",
    ),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE_CASES))
def test_policy_rejects_mutation(case):
    mutator, expected = NEGATIVE_CASES[case]
    problems = _mutated(mutator)
    assert any(expected in p for p in problems), (case, problems)


def test_malformed_toml_is_rejected(tmp_path):
    bad = tmp_path / "pyproject.toml"
    bad.write_text("[project\nname = ")
    with pytest.raises(tomllib.TOMLDecodeError):
        load(bad)


# ------------------------------------------------------------ real uv checks
uv_path = shutil.which("uv")


@pytest.mark.skipif(uv_path is None, reason="uv not installed (NOT RUN, not PASS)")
@pytest.mark.parametrize("interpreter", ["3.11", "3.13"])
def test_uv_refuses_incompatible_interpreter(tmp_path, interpreter):
    """uv itself must refuse other minor versions. Needs the interpreter
    installed locally; skipped (not passed) when it is unavailable."""
    found = subprocess.run(
        [uv_path, "python", "find", interpreter], capture_output=True, text=True, check=False
    )
    if found.returncode != 0:
        pytest.skip(f"python {interpreter} not installed locally (NOT RUN)")
    shutil.copy(PYPROJECT, tmp_path / "pyproject.toml")
    result = subprocess.run(
        [uv_path, "lock", "--offline", "--python", interpreter],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "incompatible with the project's Python requirement" in result.stderr
    assert not (tmp_path / "uv.lock").exists()
