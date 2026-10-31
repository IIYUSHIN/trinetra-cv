"""Tests for the identifiers in trinetra.domain.jobs (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.ids import IdentifierError, OpaqueId
from trinetra.domain.jobs import JobId, TestDefinitionId

CASES = [('TestDefinitionId', 'testdef'), ('JobId', 'job')]

CLASSES = {"TestDefinitionId": TestDefinitionId, "JobId": JobId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


def test_siblings_are_not_interchangeable():
    x = TestDefinitionId.generate()
    with pytest.raises(IdentifierError):
        JobId.parse(str(x))
    assert x != JobId.parse("job_" + str(x).split("_", 1)[1])
