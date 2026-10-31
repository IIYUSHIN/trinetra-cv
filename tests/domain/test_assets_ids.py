"""Tests for the identifiers in trinetra.domain.assets (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.ids import IdentifierError, OpaqueId

CASES = [('AssetId', 'asset')]

CLASSES = {"AssetId": AssetId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


# ---------------------------------------------------------------- AssetKind
def test_asset_kind_wire_forms_are_lowercase_and_stable():
    assert {k.name: k.value for k in AssetKind} == {
        "DATASET": "dataset", "MODEL": "model", "CONTRIBUTOR_METADATA": "contributor_metadata",
        "REFERENCE": "reference", "INFERENCE_RECORD": "inference_record",
        "ASSESSMENT_PROFILE": "assessment_profile", "INFERENCE_OUTPUT": "inference_output",
        "INFERENCE_RECEIPT": "inference_receipt"}
    assert all(k.value == k.value.lower() for k in AssetKind)


@pytest.mark.parametrize("bad", ["Dataset", "DATASET", " dataset", "dataset ", "", "image", None, 1])
def test_asset_kind_parsing_is_exact(bad):
    with pytest.raises(ValueError):
        AssetKind(bad)
    assert AssetKind("dataset") is AssetKind.DATASET


def test_asset_kind_inside_a_domain_model_is_strict():
    from pydantic import ValidationError

    from trinetra.domain.base import DomainModel

    class Rec(DomainModel):
        asset_id: AssetId
        asset_kind: AssetKind

    rec = Rec(asset_id=AssetId.generate(), asset_kind=AssetKind.MODEL)
    assert rec.to_wire()["asset_kind"] == "model" and Rec(**rec.to_wire()) == rec
    for bad in ("MODEL", "Model", "unknown", 3, None):
        with pytest.raises(ValidationError):
            Rec(asset_id=AssetId.generate(), asset_kind=bad)
