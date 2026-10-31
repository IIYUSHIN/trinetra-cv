"""Tests for the identifiers in trinetra.domain.policy (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.ids import IdentifierError, OpaqueId
from trinetra.domain.policy import PolicyDecisionId, PolicyProfileId

CASES = [('PolicyProfileId', 'polprof'), ('PolicyDecisionId', 'poldec')]

CLASSES = {"PolicyProfileId": PolicyProfileId, "PolicyDecisionId": PolicyDecisionId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


def test_siblings_are_not_interchangeable():
    x = PolicyProfileId.generate()
    with pytest.raises(IdentifierError):
        PolicyDecisionId.parse(str(x))
    assert x != PolicyDecisionId.parse("poldec_" + str(x).split("_", 1)[1])
