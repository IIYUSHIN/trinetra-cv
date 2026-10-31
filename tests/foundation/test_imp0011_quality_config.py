"""IMP-0011 tests: Ruff / type-check / pytest configuration.

Three layers: (1) a policy checker over pyproject.toml with mutation cases,
(2) planted-violation proofs that each configured rule really fires (a config
key that is never exercised is just a comment), and (3) the project's own
quality commands run for real.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
REQUIRED_RULE_FAMILIES = {"E", "F", "W", "I", "B", "UP", "S", "BLE", "DTZ", "PT", "G", "T20", "N"}


def load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _pin(specs: list[str], name: str) -> str | None:
    for spec in specs:
        if re.match(rf"{name}\b", spec):
            return spec
    return None


def check_quality_config(doc: dict) -> list[str]:
    """Pure policy check; returns human-readable problems (empty = conformant)."""
    problems: list[str] = []
    tool = doc.get("tool", {})

    ruff = tool.get("ruff")
    if not isinstance(ruff, dict):
        return ["tool.ruff missing: lint results must not depend on Ruff's built-in defaults"]
    req = ruff.get("required-version", "")
    if not (">=" in req and "<" in req):
        problems.append("tool.ruff.required-version needs a lower AND upper bound")
    if ruff.get("target-version") != "py312":
        problems.append("tool.ruff.target-version must be py312")
    line_length = ruff.get("line-length")
    if not isinstance(line_length, int) or isinstance(line_length, bool) or not 80 <= line_length <= 120:
        problems.append("tool.ruff.line-length must be an int in 80..120")
    if ruff.get("fix") is not False:
        problems.append("tool.ruff.fix must be false: lint must not rewrite files implicitly")
    lint = ruff.get("lint", {})
    select = lint.get("select")
    if not isinstance(select, list) or not select or "ALL" in select:
        problems.append("tool.ruff.lint.select must be an explicit non-empty list (not ALL)")
    else:
        missing = REQUIRED_RULE_FAMILIES - set(select)
        if missing:
            problems.append(f"tool.ruff.lint.select lacks required families {sorted(missing)}")
    for rule in lint.get("ignore", []):
        if rule.startswith(("S", "BLE", "DTZ", "E9", "F")) and not rule.startswith("SIM"):
            problems.append(f"tool.ruff.lint.ignore must not globally disable {rule}")
    ignores = lint.get("per-file-ignores", {})
    for pattern in ignores:
        if not pattern.startswith("tests/"):
            problems.append(f"per-file-ignores may only relax tests/, not {pattern!r}")
    if "S101" in ignores.get("tests/**", []) and "S101" in lint.get("ignore", []):
        problems.append("S101 must be exempted for tests only, not globally")
    if lint.get("isort", {}).get("known-first-party") != ["trinetra"]:
        problems.append("isort known-first-party must be ['trinetra']")

    mypy = tool.get("mypy")
    if not isinstance(mypy, dict):
        problems.append("tool.mypy missing")
    else:
        if mypy.get("strict") is not True:
            problems.append("tool.mypy.strict must be true")
        if mypy.get("python_version") != "3.12":
            problems.append("tool.mypy.python_version must be 3.12")
        # ERR-CL-014 (PROPOSED): the evaluation package is type-checked under the same strict settings.
        if mypy.get("files") != ["backend/src/trinetra", "evaluation/src/trinetra_eval"]:
            problems.append("tool.mypy.files must be exactly the production and evaluation packages")
        if mypy.get("warn_unreachable") is not True:
            problems.append("tool.mypy.warn_unreachable must be true")
        if "ignore-without-code" not in mypy.get("enable_error_code", []):
            problems.append("mypy must enable ignore-without-code (no blanket type: ignore)")
        if mypy.get("ignore_errors") or mypy.get("overrides"):
            problems.append("mypy must not silence errors via ignore_errors/overrides")

    pytest_cfg = tool.get("pytest", {}).get("ini_options")
    if not isinstance(pytest_cfg, dict):
        problems.append("tool.pytest.ini_options missing")
    else:
        addopts = pytest_cfg.get("addopts", [])
        for flag in ("--strict-markers", "--strict-config"):
            if flag not in addopts:
                problems.append(f"pytest addopts must include {flag}")
        if pytest_cfg.get("xfail_strict") is not True:
            problems.append("pytest xfail_strict must be true")
        if pytest_cfg.get("filterwarnings") != ["error"]:
            problems.append("pytest filterwarnings must be exactly ['error']")
        if pytest_cfg.get("testpaths") != ["tests"]:
            problems.append("pytest testpaths must be ['tests']")
        if not any(m.startswith("network:") for m in pytest_cfg.get("markers", [])):
            problems.append("pytest must register the 'network' marker")
        if not pytest_cfg.get("minversion"):
            problems.append("pytest minversion missing")

    lint_group = doc.get("dependency-groups", {}).get("lint", [])
    for name in ("ruff", "mypy"):
        pin = _pin(lint_group, name)
        if pin is None or "<" not in pin or ">=" not in pin:
            problems.append(f"lint group must pin {name} with lower and upper bound")
    ruff_pin = _pin(lint_group, "ruff")
    if ruff_pin and req:
        try:
            if SpecifierSet(ruff_pin.removeprefix("ruff")) != SpecifierSet(req):
                problems.append("tool.ruff.required-version must equal the lint group's ruff pin")
        except Exception:  # noqa: BLE001 - malformed specifier is itself the problem
            problems.append("ruff pins are not valid version specifiers")
    return problems


# ---------------------------------------------------------------- policy layer
def test_repository_config_conforms():
    assert check_quality_config(load()) == []


def _set(path: list[str], value):
    def mutate(doc):
        node = doc
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return mutate


def _drop(path: list[str]):
    def mutate(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return mutate


MUTATIONS = {
    "ruff section removed": (_drop(["tool", "ruff"]), "tool.ruff missing"),
    "ruff version unbounded above": (_set(["tool", "ruff", "required-version"], ">=0.17"), "AND upper"),
    "ruff version missing": (_set(["tool", "ruff", "required-version"], ""), "AND upper"),
    "wrong target": (_set(["tool", "ruff", "target-version"], "py311"), "py312"),
    "huge line length": (_set(["tool", "ruff", "line-length"], 500), "line-length"),
    "bool line length": (_set(["tool", "ruff", "line-length"], True), "line-length"),
    "autofix on": (_set(["tool", "ruff", "fix"], True), "fix must be false"),
    "select ALL": (_set(["tool", "ruff", "lint", "select"], ["ALL"]), "explicit"),
    "select empty": (_set(["tool", "ruff", "lint", "select"], []), "explicit"),
    "bandit removed": (_set(["tool", "ruff", "lint", "select"], ["E", "F", "W", "I", "B", "UP", "BLE",
                                                                   "DTZ", "PT", "G", "T20", "N"]), "'S'"),
    "global S101 ignore": (_set(["tool", "ruff", "lint", "ignore"], ["S101"]), "globally disable S101"),
    "global S301 ignore": (_set(["tool", "ruff", "lint", "ignore"], ["S301"]), "globally disable S301"),
    "global BLE ignore": (_set(["tool", "ruff", "lint", "ignore"], ["BLE001"]), "globally disable BLE001"),
    "src exempted": (_set(["tool", "ruff", "lint", "per-file-ignores"], {"backend/**": ["S101"]}),
                     "may only relax tests"),
    "isort first party": (_set(["tool", "ruff", "lint", "isort", "known-first-party"], []),
                          "known-first-party"),
    "mypy removed": (_drop(["tool", "mypy"]), "tool.mypy missing"),
    "mypy not strict": (_set(["tool", "mypy", "strict"], False), "strict must be true"),
    "mypy wrong python": (_set(["tool", "mypy", "python_version"], "3.11"), "python_version"),
    "mypy files narrowed": (_set(["tool", "mypy", "files"], ["backend/src/trinetra/errors.py"]),
                            "production and evaluation packages"),
    "mypy unreachable off": (_set(["tool", "mypy", "warn_unreachable"], False), "warn_unreachable"),
    "mypy blanket ignore": (_set(["tool", "mypy", "enable_error_code"], []), "ignore-without-code"),
    "mypy ignore_errors": (_set(["tool", "mypy", "ignore_errors"], True), "silence"),
    "mypy overrides": (_set(["tool", "mypy", "overrides"], [{"module": "trinetra.*", "ignore_errors": True}]),
                       "silence"),
    "pytest section removed": (_drop(["tool", "pytest"]), "ini_options missing"),
    "pytest strict markers off": (_set(["tool", "pytest", "ini_options", "addopts"], ["--strict-config"]),
                                  "--strict-markers"),
    "pytest strict config off": (_set(["tool", "pytest", "ini_options", "addopts"], ["--strict-markers"]),
                                 "--strict-config"),
    "pytest xfail lax": (_set(["tool", "pytest", "ini_options", "xfail_strict"], False), "xfail_strict"),
    "pytest warnings tolerated": (_set(["tool", "pytest", "ini_options", "filterwarnings"], ["default"]),
                                  "filterwarnings"),
    "pytest testpaths": (_set(["tool", "pytest", "ini_options", "testpaths"], ["."]), "testpaths"),
    "pytest marker missing": (_set(["tool", "pytest", "ini_options", "markers"], []), "'network'"),
    "pytest minversion": (_drop(["tool", "pytest", "ini_options", "minversion"]), "minversion"),
    "ruff unpinned in group": (_set(["dependency-groups", "lint"], ["ruff", "mypy>=2.4,<3"]), "pin ruff"),
    "mypy unpinned in group": (_set(["dependency-groups", "lint"], ["ruff>=0.17,<0.18", "mypy"]), "pin mypy"),
    "ruff pin mismatch": (_set(["dependency-groups", "lint"], ["ruff>=0.16,<0.18", "mypy>=2.4,<3"]),
                          "must equal"),
    "mypy missing from group": (_set(["dependency-groups", "lint"], ["ruff>=0.17,<0.18"]), "pin mypy"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_policy_rejects_each_mutation(name):
    mutate, expected = MUTATIONS[name]
    doc = copy.deepcopy(load())
    mutate(doc)
    problems = check_quality_config(doc)
    assert any(expected in p for p in problems), (name, problems)


def test_gitignore_covers_tool_caches():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in (".hypothesis/", ".mypy_cache/", ".ruff_cache/", ".pytest_cache/"):
        assert entry in ignored


def test_locked_tool_versions_satisfy_the_pins():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = {p["name"]: Version(p["version"]) for p in lock["package"]}
    group = load()["dependency-groups"]["lint"]
    for name in ("ruff", "mypy"):
        spec = SpecifierSet(_pin(group, name).removeprefix(name))
        assert name in versions and versions[name] in spec, (name, versions.get(name), str(spec))


# ------------------------------------------------- planted-violation proofs
def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


needs_ruff = pytest.mark.skipif(not _have("ruff"), reason="ruff not installed (NOT RUN, not PASS)")
needs_mypy = pytest.mark.skipif(not _have("mypy"), reason="mypy not installed (NOT RUN, not PASS)")


def run(args: list[str], *, stdin: str | None = None, cwd: Path = ROOT):
    return subprocess.run([sys.executable, *args], input=stdin, capture_output=True, text=True,
                          cwd=cwd, check=False, timeout=120)


def ruff_stdin(snippet: str, as_file: str) -> subprocess.CompletedProcess[str]:
    return run(["-m", "ruff", "check", "--no-cache", "--output-format", "concise",
                "--stdin-filename", as_file, "-"], stdin=snippet)


PLANTED = {
    "S301": "import pickle\npickle.loads(b'')\n",
    "DTZ005": "from datetime import datetime\n\nvalue = datetime.now()\n",
    "BLE001": "try:\n    value = 1\nexcept Exception:\n    value = 2\n",
    "T201": "print('x')\n",
    "B006": "def f(a=[]):\n    return a\n",
    "E501": "value = '" + "a" * 120 + "'\n",
    "F401": "import os\n",
    "UP006": "from typing import List\n\n\ndef f(x: List[int]) -> None:\n    return None\n",
    "I001": "import sys\nimport os\n\nprint(sys, os)\n",
    "G004": "import logging\nlogging.getLogger(__name__).info(f'x {1}')\n",
    "S603": "import subprocess\n\n\ndef f(cmd: list[str]) -> None:\n    subprocess.run(cmd, check=False)\n",
    "N802": "def BadName():\n    return 1\n",
}


@needs_ruff
@pytest.mark.parametrize("code", sorted(PLANTED))
def test_configured_rule_really_fires_in_production_code(code):
    result = ruff_stdin(PLANTED[code], "backend/src/trinetra/planted.py")
    assert result.returncode == 1 and code in result.stdout, (code, result.stdout, result.stderr)


@needs_ruff
def test_assert_is_allowed_in_tests_but_not_in_production():
    snippet = "def test_x():\n    assert 1 + 1 == 2\n"
    assert "S101" not in ruff_stdin(snippet, "tests/foundation/test_planted.py").stdout
    prod = ruff_stdin(snippet, "backend/src/trinetra/planted.py")
    assert prod.returncode == 1 and "S101" in prod.stdout


@needs_ruff
def test_clean_snippet_passes_under_the_project_config():
    clean = '"""Doc."""\n\nVALUE = 1\n'
    result = ruff_stdin(clean, "backend/src/trinetra/planted.py")
    assert result.returncode == 0, result.stdout


@needs_ruff
def test_ruff_does_not_depend_on_user_level_config(tmp_path):
    """Config discovery must come from the repository, not from ~/.config/ruff."""
    home = tmp_path / "home"
    (home / ".config" / "ruff").mkdir(parents=True)
    (home / ".config" / "ruff" / "ruff.toml").write_text('[lint]\nselect = ["F401"]\nignore = ["S301"]\n')
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "concise",
         "--stdin-filename", "backend/src/trinetra/planted.py", "-"],
        input=PLANTED["S301"], capture_output=True, text=True, cwd=ROOT, env=env, check=False)
    assert result.returncode == 1 and "S301" in result.stdout


@needs_mypy
@pytest.mark.parametrize(
    ("snippet", "code"),
    [
        ("def f(x):\n    return x\n", "no-untyped-def"),
        ("x: int = 'a'\n", "assignment"),
        ("def f(x: int) -> int:\n    return x\n\nf('a')\n", "arg-type"),
        ("x: int = 'a'  # type: ignore\n", "ignore-without-code"),
        ("def f(x: int) -> int:\n    if isinstance(x, int):\n        return x\n    return x\n",
         "unreachable"),
        ("def f(items: list[int]) -> bool:\n    return not items or items[0].real is None\n", None),
    ],
)
def test_mypy_strictness_really_fires(tmp_path, snippet, code):
    target = tmp_path / "planted.py"
    target.write_text(snippet)
    result = run(["-m", "mypy", "--config-file", str(PYPROJECT), "--no-incremental",
                  "--cache-dir", "/dev/null", str(target)], cwd=tmp_path)
    if code is None:
        assert result.returncode in (0, 1)
    else:
        assert result.returncode == 1 and f"[{code}]" in result.stdout, result.stdout


# ------------------------------------------------ pytest settings are live
def run_pytest_on(tmp_path: Path, body: str):
    test_file = tmp_path / "test_planted.py"
    test_file.write_text(textwrap.dedent(body))
    return run(["-m", "pytest", "-c", str(PYPROJECT), "--rootdir", str(tmp_path), str(test_file)],
               cwd=tmp_path)


def test_unknown_marker_is_an_error(tmp_path):
    result = run_pytest_on(tmp_path, """
        import pytest

        @pytest.mark.not_registered
        def test_a():
            pass
    """)
    assert result.returncode != 0 and "not_registered" in result.stdout + result.stderr


def test_warnings_are_errors(tmp_path):
    result = run_pytest_on(tmp_path, """
        import warnings

        def test_a():
            warnings.warn("careless", UserWarning, stacklevel=1)
    """)
    assert result.returncode == 1 and "careless" in result.stdout


def test_unexpected_pass_of_xfail_fails(tmp_path):
    result = run_pytest_on(tmp_path, """
        import pytest

        @pytest.mark.xfail
        def test_a():
            assert True
    """)
    assert result.returncode == 1 and "XPASS" in result.stdout


def test_registered_network_marker_is_accepted(tmp_path):
    result = run_pytest_on(tmp_path, """
        import pytest

        @pytest.mark.network
        def test_a():
            assert True
    """)
    assert result.returncode == 0, result.stdout


def test_misspelled_ini_key_is_an_error(tmp_path):
    bad = tmp_path / "pyproject.toml"
    bad.write_text('[tool.pytest.ini_options]\naddopts = ["--strict-config"]\nnot_a_real_option = 1\n')
    (tmp_path / "test_a.py").write_text("def test_a():\n    pass\n")
    result = run(["-m", "pytest", "-c", str(bad), "--rootdir", str(tmp_path), str(tmp_path / "test_a.py")],
                 cwd=tmp_path)
    assert result.returncode != 0 and "not_a_real_option" in result.stdout + result.stderr


# ---------------------------------------------- the real quality commands
@needs_ruff
def test_ruff_check_passes_on_the_whole_repository():
    result = run(["-m", "ruff", "check", "--no-cache", "."])
    assert result.returncode == 0, result.stdout + result.stderr


@needs_mypy
def test_mypy_strict_passes_on_the_production_package():
    result = run(["-m", "mypy", "--no-incremental", "--cache-dir", "/dev/null"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues found" in result.stdout
