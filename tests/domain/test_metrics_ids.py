"""Tests for the identifiers in trinetra.domain.metrics (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.ids import IdentifierError, OpaqueId
from trinetra.domain.metrics import MetricId

CASES = [('MetricId', 'metric')]

CLASSES = {"MetricId": MetricId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])
