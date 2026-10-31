"""Catalog-wide checks over every production identifier type (grows with each ID packet)."""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

import pytest

import trinetra.domain
from trinetra.domain import ids
from trinetra.domain.base import DomainModel
from trinetra.domain.ids import IdentifierError

GOLDEN = json.loads((Path(__file__).parent / "golden" / "id_prefixes.json").read_text("utf-8"))
for _info in pkgutil.walk_packages(trinetra.domain.__path__, "trinetra.domain."):
    importlib.import_module(_info.name)
PRODUCTION = {p: c for p, c in ids._REGISTRY.items() if c.__module__.startswith("trinetra.")}
HEX = "0123456789abcdef0123456789abcdef"


def test_golden_manifest_matches_registered_production_identifiers():
    assert {p: f"{c.__module__}.{c.__qualname__}" for p, c in PRODUCTION.items()} == GOLDEN
    assert list(GOLDEN) == sorted(GOLDEN)


@pytest.mark.parametrize("prefix", sorted(PRODUCTION))
def test_every_identifier_roundtrips_and_is_distinct_from_every_other(prefix):
    cls = PRODUCTION[prefix]
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    for other_prefix, other in PRODUCTION.items():
        if other is cls:
            continue
        with pytest.raises(IdentifierError):
            other.parse(str(ident))
        twin = other.parse(f"{other_prefix}_{str(ident).split('_', 1)[1]}")
        assert twin != ident and hash(twin) != hash(ident)


@pytest.mark.parametrize("prefix", sorted(PRODUCTION))
def test_every_identifier_is_strict_inside_a_domain_model(prefix):
    cls = PRODUCTION[prefix]
    holder = type("Holder", (DomainModel,), {"__annotations__": {"ref": cls}})
    ident = cls.generate()
    assert holder(ref=ident).to_wire() == {"ref": str(ident)}
    assert holder(**holder(ref=ident).to_wire()).ref == ident
    for other_prefix, other in PRODUCTION.items():
        if other is not cls:
            with pytest.raises(ValueError, match="ref"):
                holder(ref=other.generate())
            with pytest.raises(ValueError, match="ref"):
                holder(ref=f"{other_prefix}_{HEX}")
