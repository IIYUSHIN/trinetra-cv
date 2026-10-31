"""IMP-0004 tests: backend package bootstrap and import boundary.

The real tree must conform; every rule must also be shown to fire on a
synthetic tree built in tmp_path (so a checker that approves everything cannot
keep this suite green).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
from test_imp0002_project_metadata import PYPROJECT, REPO_ROOT

import trinetra
from trinetra.boundaries import LAYERS, check_tree

SRC = REPO_ROOT / "backend" / "src"
EXPECTED_DIRS = {
    "domain", "application", "infrastructure", "api", "adapters", "methods",
    "methods/data", "methods/model", "methods/shift", "execution", "tiir", "assurance",
    # PROPOSED amendment (ERR-CL-012, IMP-0044): Alembic revision scripts ship inside the package so an
    # offline release carries its own schema authority. The Bible's repository shape does not place them.
    "infrastructure/migrations", "infrastructure/migrations/versions",
    # PROPOSED amendment (ERR-CL-022, IMP-0071): the later EIB-04 file tree places the intake workflow in
    # application/intake. Only subpackages that actually exist are listed; each addition is an ERR record.
    "application/intake", "application/ports",
}


# ------------------------------------------------------------------ bootstrap
def test_real_tree_conforms():
    assert check_tree(SRC) == []


def test_layer_directories_match_bible_repository_shape():
    found = {
        p.relative_to(SRC / "trinetra").as_posix()
        for p in (SRC / "trinetra").rglob("*")
        if p.is_dir() and "__pycache__" not in p.parts
    }
    assert found == EXPECTED_DIRS
    assert {d.split("/")[0] for d in EXPECTED_DIRS} == set(LAYERS)
    for d in EXPECTED_DIRS:
        assert (SRC / "trinetra" / d / "__init__.py").is_file()


def test_version_matches_pyproject():
    with PYPROJECT.open("rb") as fh:
        assert trinetra.__version__ == tomllib.load(fh)["project"]["version"]


def test_py_typed_marker_present_and_empty():
    marker = SRC / "trinetra" / "py.typed"
    assert marker.is_file() and marker.stat().st_size == 0


def test_import_has_no_side_effects_or_third_party_imports():
    """A clean interpreter importing trinetra loads no layer and no 3rd party."""
    code = textwrap.dedent("""
        import sys
        before = set(sys.modules)
        import trinetra
        new = sorted(m for m in set(sys.modules) - before)
        bad = [m for m in new if m.split('.')[0] != 'trinetra']
        layers = [m for m in new if m.startswith('trinetra.')]
        print(bad, layers)
        raise SystemExit(1 if (bad or layers) else 0)
    """)
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_subpackage_imports_cleanly_in_isolation():
    names = ["trinetra.boundaries"] + [
        "trinetra." + d.replace("/", ".") for d in sorted(EXPECTED_DIRS)]
    code = "import importlib,sys\n" + "\n".join(f"importlib.import_module({n!r})" for n in names)
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, result.stderr


def test_boundaries_module_is_stdlib_only_and_really_audited(tmp_path):
    """Root-level modules must be analysed, not skipped: prove it by planting a
    third-party import at the root and by auditing a copy of boundaries.py."""
    assert check_tree(SRC) == []
    planted = build(tmp_path, {"leak.py": "import pydantic\n"})
    assert "DEP-002" in rules(planted)
    copy = build(tmp_path / "c", {})
    (copy / "trinetra" / "boundaries.py").write_text(
        (SRC / "trinetra" / "boundaries.py").read_text() + "\nimport numpy\n")
    assert "DEP-002" in rules(copy)


# ------------------------------------------------------- synthetic-tree helpers
def build(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create tmp/src/trinetra with every layer package plus the given files."""
    pkg = tmp_path / "src" / "trinetra"
    for d in sorted(EXPECTED_DIRS):
        (pkg / d).mkdir(parents=True, exist_ok=True)
        (pkg / d / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    for rel, body in files.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body))
    return tmp_path / "src"


def rules(src: Path) -> set[str]:
    return {v.rule for v in check_tree(src)}


def test_clean_synthetic_tree_passes(tmp_path):
    src = build(tmp_path, {
        "domain/ids.py": "from pydantic import BaseModel\nimport dataclasses\n",
        "application/use.py": "from trinetra.domain import ids\nfrom ..domain import ids as i2\n",
        "infrastructure/db.py": "import sqlite3\nfrom sqlalchemy import select\n"
                                "from trinetra.application import use\n",
        "api/routes.py": "from fastapi import APIRouter\nfrom trinetra.application import use\n",
        "tiir/sign.py": "import cbor2\nfrom cryptography.hazmat.primitives import hashes\n",
        "execution/sup.py": "import subprocess\n",
    })
    assert check_tree(src) == []


# ---------------------------------------------------------------- negatives
LAYER_VIOLATIONS = {
    "domain_imports_application": ("domain/x.py", "from trinetra.application import y\n",
                                   "DOM-001"),
    "domain_imports_api_relative": ("domain/x.py", "from ..api import routes\n", "DOM-001"),
    "domain_imports_fastapi": ("domain/x.py", "import fastapi\n", "DEP-002"),
    "domain_imports_sqlalchemy": ("domain/x.py", "from sqlalchemy import Table\n", "DEP-002"),
    "domain_imports_os": ("domain/x.py", "import os\n", "DOM-002"),
    "domain_imports_importlib": ("domain/x.py", "from importlib import util\n", "DOM-002"),
    "application_imports_infrastructure": (
        "application/x.py", "from trinetra.infrastructure import db\n", "APP-001"),
    "application_imports_adapters": ("application/x.py", "import trinetra.adapters\n", "APP-001"),
    "application_imports_fastapi": ("application/x.py", "import fastapi\n", "DEP-002"),
    "infrastructure_imports_api": (
        "infrastructure/x.py", "from trinetra.api import routes\n", "API-001"),
    "methods_imports_api": ("methods/data/x.py", "from trinetra import api\n", "API-001"),
    "root_imports_layer": ("helper.py", "from trinetra import domain\n", "ROOT-001"),
    "production_imports_evaluation": ("assurance/x.py", "import trinetra_eval\n", "EVAL-001"),
    "production_imports_runner": ("execution/x.py", "from runner import main\n", "EVAL-001"),
    "torch_in_core": ("adapters/x.py", "import torch\n", "DEP-001"),
    "onnxruntime_in_core": ("adapters/x.py", "import onnxruntime\n", "DEP-001"),
    "requests_unlisted": ("api/x.py", "import requests\n", "DEP-001"),
    "telemetry_import": ("api/x.py", "import sentry_sdk\n", "DEP-001"),
    "fastapi_outside_api": ("infrastructure/x.py", "import fastapi\n", "DEP-002"),
    "sqlalchemy_outside_infra": ("tiir/x.py", "import sqlalchemy\n", "DEP-002"),
    "cryptography_in_adapters": ("adapters/x.py", "import cryptography\n", "DEP-002"),
    "pickle_anywhere": ("adapters/x.py", "import pickle\n", "STD-001"),
    "marshal_anywhere": ("tiir/x.py", "from marshal import loads\n", "STD-001"),
    "socket_anywhere": ("api/x.py", "import socket\n", "STD-001"),
    "urllib_download": ("infrastructure/x.py", "import urllib.request\n", "STD-001"),
    "subprocess_outside_execution": ("infrastructure/x.py", "import subprocess\n", "STD-001"),
    "sqlite_outside_infrastructure": ("application/x.py", "import sqlite3\n", "STD-001"),
    "http_outside_api": ("domain/x.py", "import http.client\n", "STD-001"),
    "dunder_import": ("adapters/x.py", "m = __import__('socket')\n", "DYN-001"),
    "import_module_call": (
        "adapters/x.py", "import importlib\nimportlib.import_module('x')\n", "DYN-001"),
    "bare_import_module_call": (
        "adapters/x.py", "from importlib import import_module\nimport_module('x')\n", "DYN-001"),
    "eval_call": ("domain/x.py", "eval('1')\n", "DYN-001"),
    "exec_call": ("domain/x.py", "exec('1')\n", "DYN-001"),
    "type_checking_import_counts": (
        "domain/x.py",
        ("from typing import TYPE_CHECKING\n"
         "if TYPE_CHECKING:\n    from trinetra.application import a\n"),
        "DOM-001"),
    "function_local_import_counts": (
        "domain/x.py", "def f():\n    import fastapi\n", "DEP-002"),
    "syntax_error_fails_closed": ("domain/x.py", "def (:\n", "PARSE-001"),
    "relative_escape": ("domain/x.py", "from ... import z\n", "PARSE-002"),
    "unknown_layer_import": ("domain/x.py", "import trinetra.nonsense\n", "BOOT-003"),
}


@pytest.mark.parametrize("case", sorted(LAYER_VIOLATIONS))
def test_rule_fires(tmp_path, case):
    rel, body, rule = LAYER_VIOLATIONS[case]
    assert rule in rules(build(tmp_path, {rel: body})), (case, check_tree(tmp_path / "src"))


def test_violation_reports_file_and_line(tmp_path):
    src = build(tmp_path, {"domain/x.py": "import math\n\nimport fastapi\n"})
    [v] = check_tree(src)
    assert (v.path, v.line, v.rule) == ("trinetra/domain/x.py", 3, "DEP-002")


def test_import_cycle_between_peer_layers_is_rejected(tmp_path):
    src = build(tmp_path, {
        "infrastructure/a.py": "from trinetra.assurance import b\n",
        "assurance/b.py": "from trinetra.infrastructure import a\n",
    })
    found = [v for v in check_tree(src) if v.rule == "CYCLE-001"]
    assert found and "infrastructure" in found[0].detail and "assurance" in found[0].detail


def test_acyclic_peer_imports_are_not_restricted(tmp_path):
    src = build(tmp_path, {"assurance/b.py": "from trinetra.infrastructure import a\n"})
    assert check_tree(src) == []


# ------------------------------------------------------------ structure faults
def test_missing_package_directory_fails_closed(tmp_path):
    assert {v.rule for v in check_tree(tmp_path)} == {"BOOT-001"}


def test_unknown_layer_directory_is_rejected(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "backdoor_detector").mkdir()
    (src / "trinetra" / "backdoor_detector" / "__init__.py").write_text("")
    assert "BOOT-003" in rules(src)


def test_package_without_init_is_rejected(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "domain" / "sub").mkdir()
    (src / "trinetra" / "domain" / "sub" / "m.py").write_text("")
    assert "BOOT-004" in rules(src)


def test_non_python_file_is_rejected(tmp_path):
    src = build(tmp_path, {"domain/data.json": "{}"})
    assert "BOOT-005" in rules(src)


def test_symlink_in_package_is_rejected(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "domain" / "link.py").symlink_to(src / "trinetra" / "__init__.py")
    assert "BOOT-002" in rules(src)


def test_non_utf8_source_fails_closed(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "domain" / "bad.py").write_bytes(b"\xff\xfe\x00bad")
    assert "PARSE-001" in rules(src)


def test_null_byte_source_fails_closed(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "domain" / "nul.py").write_bytes(b"x = 1\x00\n")
    assert "PARSE-001" in rules(src)


def test_symlinked_package_root_is_rejected(tmp_path):
    real = build(tmp_path / "real", {})
    link = tmp_path / "linked"
    link.mkdir()
    (link / "trinetra").symlink_to(real / "trinetra")
    assert {v.rule for v in check_tree(link)} == {"BOOT-001"}


def test_pydantic_core_follows_pydantic_exactly(tmp_path):
    """pydantic_core is pydantic's own dependency: allowed in every layer, not in root modules."""
    from trinetra.boundaries import THIRD_PARTY

    assert THIRD_PARTY["pydantic_core"] == THIRD_PARTY["pydantic"]
    assert check_tree(build(tmp_path, {"domain/x.py": "import pydantic_core\n"})) == []
    assert "DEP-002" in rules(build(tmp_path / "r", {"errors.py": "import pydantic_core\n"}))
