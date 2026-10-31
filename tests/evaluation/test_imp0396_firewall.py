"""Import boundary falsification: direct, aliased, relative and dynamic imports."""

import pytest

from trinetra_eval.firewall import audit_source


@pytest.mark.parametrize(
    "source",
    [
        "import trinetra_eval",
        "from trinetra_eval.hidden import Truth",
        "import trinetra_eval as safe",
        "from . import trinetra_eval",
    ],
)
def test_production_import_is_rejected(source):
    assert audit_source(source, production=True)


def test_evaluator_can_import_primitives_but_not_assurance_logic():
    assert audit_source("from trinetra.domain.claim_state import ClaimState", production=False) == ()
    assert audit_source("from trinetra.assurance import decision", production=False)


@pytest.mark.parametrize(
    "source",
    [
        "broken syntax !",
        "__import__(name)",
        "loader.import_module(name)",
        "eval(code)",
        "from package import *",
    ],
)
def test_unknown_dependencies_fail_closed(source):
    assert audit_source(source, production=True)


def test_empty_source_and_unrelated_import_are_not_violations():
    assert audit_source("", production=True) == ()
    assert audit_source("import json", production=True) == ()


@pytest.mark.parametrize(
    "source",
    [
        "from importlib import import_module as load; load('trinetra_eval.hidden')",
        "load = __import__; load('trinetra_eval.hidden')",
        "import importlib as loader; getattr(loader, 'import_module')(name)",
    ],
)
def test_aliased_dynamic_imports_are_not_a_firewall_escape(source):
    assert audit_source(source, production=True)


def test_from_package_import_does_not_hide_assurance_dependency():
    assert audit_source("from trinetra import assurance", production=False)


def test_regular_expression_compilation_is_not_dynamic_python_execution():
    assert audit_source("import re; pattern = re.compile('x')", production=True) == ()
