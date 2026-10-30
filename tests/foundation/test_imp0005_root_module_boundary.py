"""IMP-0005 tests: root-module classification in the import-boundary checker.

`trinetra.errors` must be importable from every layer, while unknown root
modules, unregistered root files, wildcard imports and cycles stay rejected.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from test_imp0004_import_boundary import EXPECTED_DIRS, SRC, build, rules
from trinetra.boundaries import LAYERS, ROOT_IMPORTABLE, ROOT_MODULES, check_tree


def test_real_tree_still_conforms_with_errors_module_present():
    assert (SRC / "trinetra" / "errors.py").is_file()
    assert check_tree(SRC) == []


def test_root_registry_matches_files_on_disk():
    on_disk = {p.stem for p in (SRC / "trinetra").glob("*.py") if p.stem != "__init__"}
    assert on_disk == set(ROOT_MODULES)
    assert set(ROOT_IMPORTABLE) <= set(ROOT_MODULES)
    assert not set(ROOT_MODULES) & set(LAYERS)


@pytest.mark.parametrize("layer", sorted({d.split("/")[0] for d in EXPECTED_DIRS}))
@pytest.mark.parametrize(
    "stmt",
    [
        "from trinetra.errors import TrinetraError",
        "import trinetra.errors",
        "from trinetra import errors",
        "from trinetra import __version__",
    ],
)
def test_every_layer_may_import_the_root_error_module(tmp_path, layer, stmt):
    assert check_tree(build(tmp_path, {f"{layer}/x.py": stmt + "\n"})) == []


@pytest.mark.parametrize(
    ("rel", "stmt"),
    [
        ("domain/x.py", "from ..errors import TrinetraError"),
        ("methods/data/x.py", "from ...errors import TrinetraError"),
        ("methods/data/y.py", "from trinetra.errors import TrinetraError"),
    ],
)
def test_relative_import_of_root_module_resolves(tmp_path, rel, stmt):
    assert check_tree(build(tmp_path, {rel: stmt + "\n"})) == []


def test_relative_import_overshooting_the_package_fails_closed(tmp_path):
    assert "PARSE-002" in rules(build(tmp_path, {"domain/x.py": "from .... import errors\n"}))


def test_layer_local_relative_import_is_not_confused_with_root(tmp_path):
    src = build(tmp_path, {"domain/errors.py": "", "domain/x.py": "from .errors import y\n"})
    assert check_tree(src) == []


# ----------------------------------------------------------------- negatives
@pytest.mark.parametrize(
    ("stmt", "rule"),
    [
        ("import trinetra.secrets", "BOOT-003"),
        ("from trinetra import secrets", "BOOT-003"),
        ("from trinetra.secrets import token", "BOOT-003"),
        ("import trinetra.boundaries", "ROOT-002"),
        ("from trinetra import boundaries", "ROOT-002"),
        ("from trinetra.boundaries import check_tree", "ROOT-002"),
        ("from trinetra import *", "WILD-001"),
        ("from trinetra.errors import *", "WILD-001"),
        ("from trinetra.domain import *", "WILD-001"),
        ("from os.path import *", "WILD-001"),
    ],
)
def test_root_and_wildcard_rules_fire(tmp_path, stmt, rule):
    assert rule in rules(build(tmp_path, {"adapters/x.py": stmt + "\n"})), stmt


def test_unregistered_root_module_is_rejected(tmp_path):
    src = build(tmp_path, {"secrets.py": "X = 1\n"})
    found = [v for v in check_tree(src) if v.rule == "BOOT-006"]
    assert found and "secrets" in found[0].detail


def test_registering_a_root_module_is_what_makes_it_legal(tmp_path, monkeypatch):
    import trinetra.boundaries as b

    src = build(tmp_path, {"clock.py": "", "domain/x.py": "from trinetra.clock import now\n"})
    assert {"BOOT-006", "BOOT-003"} <= rules(src)
    monkeypatch.setattr(b, "ROOT_MODULES", b.ROOT_MODULES | {"clock"})
    monkeypatch.setattr(b, "ROOT_IMPORTABLE", b.ROOT_IMPORTABLE | {"clock"})
    assert b.check_tree(src) == []


def test_root_module_may_not_import_a_layer(tmp_path):
    src = build(tmp_path, {"errors.py": "from trinetra.domain import ids\n"})
    assert "ROOT-001" in rules(src)


def test_root_module_may_not_use_third_party(tmp_path):
    assert "DEP-002" in rules(build(tmp_path, {"errors.py": "import pydantic\n"}))


def test_root_module_may_not_use_dynamic_import(tmp_path):
    assert "DYN-001" in rules(build(tmp_path, {"errors.py": "__import__('os')\n"}))


def test_cycle_through_layers_still_detected_with_root_imports_present(tmp_path):
    src = build(tmp_path, {
        "infrastructure/a.py": "from trinetra.errors import TrinetraError\n"
                               "from trinetra.assurance import b\n",
        "assurance/b.py": "from trinetra.errors import TrinetraError\n"
                          "from trinetra.infrastructure import a\n",
    })
    assert "CYCLE-001" in rules(src)


def test_unknown_layer_check_is_not_weakened(tmp_path):
    src = build(tmp_path, {})
    (src / "trinetra" / "backdoor").mkdir()
    (src / "trinetra" / "backdoor" / "__init__.py").write_text("")
    assert "BOOT-003" in rules(src)


def test_forbidden_modules_still_rejected_alongside_errors(tmp_path):
    body = textwrap.dedent("""
        from trinetra.errors import TrinetraError
        import pickle
        import trinetra_eval
    """)
    assert {"STD-001", "EVAL-001"} <= rules(build(tmp_path, {"tiir/x.py": body}))


def test_errors_module_itself_is_audited_not_skipped(tmp_path):
    planted = Path(tmp_path) / "src"
    build(tmp_path, {})
    (planted / "trinetra" / "errors.py").write_text(
        (SRC / "trinetra" / "errors.py").read_text() + "\nimport requests\n")
    assert "DEP-001" in rules(planted)


@pytest.mark.parametrize(
    ("stmt", "flagged"),
    [
        ("import re\nre.compile('a')", False),
        ("compile('1', 'f', 'eval')", True),
        ("import builtins\nbuiltins.compile('1', 'f', 'eval')", True),
        ("import re as r\nr.compile('a')", True),  # alias: fail closed
        ("eval('1')", True),
    ],
)
def test_re_compile_is_not_dynamic_code_but_everything_else_still_is(tmp_path, stmt, flagged):
    assert ("DYN-001" in rules(build(tmp_path, {"errors.py": stmt + "\n"}))) is flagged
