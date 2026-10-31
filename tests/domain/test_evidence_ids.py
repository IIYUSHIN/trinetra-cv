"""Tests for the identifiers in trinetra.domain.evidence (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.evidence import EvidenceGroupId, EvidenceId
from trinetra.domain.ids import IdentifierError, OpaqueId

CASES = [('EvidenceId', 'evid'), ('EvidenceGroupId', 'evgrp')]

CLASSES = {"EvidenceId": EvidenceId, "EvidenceGroupId": EvidenceGroupId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


def test_siblings_are_not_interchangeable():
    x = EvidenceId.generate()
    with pytest.raises(IdentifierError):
        EvidenceGroupId.parse(str(x))
    assert x != EvidenceGroupId.parse("evgrp_" + str(x).split("_", 1)[1])
