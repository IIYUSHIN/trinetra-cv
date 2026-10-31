"""IMP-0073 tests: each package slot has one fixed contract of kind, cardinality and declared formats."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.slots import (
    MAX_TRANSFERS_PER_INTAKE,
    SLOT_CONTRACTS,
    DeclaredFormat,
    SlotContract,
    slot_contract,
)
from trinetra.domain.assets import AssetKind


def test_every_slot_has_exactly_one_contract():
    assert set(SLOT_CONTRACTS) == set(PackageSlot)
    for slot in PackageSlot:
        assert slot_contract(slot).slot is slot


def test_cardinalities_are_the_frozen_ones():
    got = {s.value: (c.minimum, c.maximum) for s, c in SLOT_CONTRACTS.items()}
    assert got == {
        "dataset": (1, 1),
        "model": (1, 1),
        "contributor_metadata": (0, 1),
        "reference": (0, MAX_TRANSFERS_PER_INTAKE),
        "inference_records": (0, MAX_TRANSFERS_PER_INTAKE),
        "assessment_profile": (0, 1),
    }


def test_each_slot_maps_to_its_asset_kind_and_no_two_share_one():
    kinds = {s.value: c.asset_kind for s, c in SLOT_CONTRACTS.items()}
    assert kinds == {
        "dataset": AssetKind.DATASET,
        "model": AssetKind.MODEL,
        "contributor_metadata": AssetKind.CONTRIBUTOR_METADATA,
        "reference": AssetKind.REFERENCE,
        "inference_records": AssetKind.INFERENCE_RECORD,
        "assessment_profile": AssetKind.ASSESSMENT_PROFILE,
    }
    assert len(set(kinds.values())) == len(kinds)


def test_allowed_declared_formats_are_the_frozen_baseline():
    def allowed(slot):
        formats = SLOT_CONTRACTS[slot].allowed_formats
        return None if formats is None else {f.text for f in formats}

    assert allowed(PackageSlot.DATASET) == {"coco", "yolo"}
    assert allowed(PackageSlot.MODEL) == {"pytorch", "torchscript", "onnx"}
    assert allowed(PackageSlot.CONTRIBUTOR_METADATA) == {"json"}
    assert allowed(PackageSlot.ASSESSMENT_PROFILE) == {"json"}
    assert allowed(PackageSlot.REFERENCE) is None
    assert allowed(PackageSlot.INFERENCE_RECORDS) is None


@pytest.mark.parametrize(
    ("slot", "fmt", "ok"),
    [
        (PackageSlot.DATASET, "coco", True), (PackageSlot.DATASET, "yolo", True),
        (PackageSlot.DATASET, "onnx", False), (PackageSlot.DATASET, "voc", False),
        (PackageSlot.MODEL, "onnx", True), (PackageSlot.MODEL, "pytorch", True),
        (PackageSlot.MODEL, "torchscript", True), (PackageSlot.MODEL, "coco", False),
        (PackageSlot.CONTRIBUTOR_METADATA, "json", True), (PackageSlot.CONTRIBUTOR_METADATA, "yaml", False),
        (PackageSlot.ASSESSMENT_PROFILE, "json", True), (PackageSlot.ASSESSMENT_PROFILE, "onnx", False),
        (PackageSlot.REFERENCE, "anything_goes", True), (PackageSlot.INFERENCE_RECORDS, "csv", True),
    ],
)  # fmt: skip
def test_a_declared_format_must_be_allowed_by_its_slot(slot, fmt, ok):
    assert slot_contract(slot).allows(DeclaredFormat(fmt)) is ok


def test_declaring_nothing_is_allowed_everywhere_and_never_guessed():
    for slot in PackageSlot:
        assert slot_contract(slot).allows(None) is True


def test_a_declared_format_is_a_canonical_token_not_free_text():
    for bad in ("", "COCO", "co co", "1coco", "a" * 65):
        with pytest.raises(Exception, match="declared_format"):
            DeclaredFormat(bad)
    assert DeclaredFormat("coco") != DeclaredFormat("yolo")


def test_the_contract_table_cannot_be_modified():
    with pytest.raises(TypeError):
        SLOT_CONTRACTS[PackageSlot.DATASET] = SLOT_CONTRACTS[PackageSlot.MODEL]  # type: ignore[index]
    with pytest.raises(ValidationError):
        SLOT_CONTRACTS[PackageSlot.DATASET].minimum = 0


@pytest.mark.parametrize(
    "bad",
    [
        {"minimum": 2, "maximum": 1},
        {"minimum": -1},
        {"maximum": 0},
        {"maximum": MAX_TRANSFERS_PER_INTAKE + 1},
        {"allowed_formats": ()},
        {"allowed_formats": (DeclaredFormat("onnx"), DeclaredFormat("coco"))},  # unsorted
        {"allowed_formats": (DeclaredFormat("coco"), DeclaredFormat("coco"))},  # duplicate
    ],
)
def test_incoherent_contracts_cannot_be_built(bad):
    values = {"slot": PackageSlot.DATASET, "asset_kind": AssetKind.DATASET, "minimum": 1, "maximum": 1,
              "allowed_formats": (DeclaredFormat("coco"),)} | bad  # fmt: skip
    with pytest.raises(ValidationError):
        SlotContract(**values)


def test_the_transfer_ceiling_is_a_documented_abuse_bound():
    assert MAX_TRANSFERS_PER_INTAKE == 1_000
