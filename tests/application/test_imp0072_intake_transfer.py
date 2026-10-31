"""IMP-0072 tests: a transfer is a small state machine whose state always matches its evidence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intake_support import entropy, transfer
from trinetra.application.errors import IntakeStateConflictError
from trinetra.application.intake.enums import PackageSlot, TransferState
from trinetra.application.intake.records import IntakeTransfer, OriginalFilename, TransferErrorCode
from trinetra.domain.assets import AssetId

RECEIVED, ADMITTED, FAILED = TransferState.RECEIVED, TransferState.ADMITTED, TransferState.FAILED


def test_a_received_transfer_has_no_admitted_asset_and_no_error():
    t = transfer()
    assert (t.transfer_state, t.admitted_asset_id, t.error_code) == (RECEIVED, None, None)
    assert t.counts is True


def test_admission_binds_exactly_the_reserved_asset_id():
    t = transfer()
    a = t.admitted()
    assert a.transfer_state is ADMITTED
    assert a.admitted_asset_id == t.reserved_asset_id
    assert a.counts is True
    assert a.evolve(transfer_state=ADMITTED) == a  # revalidation is stable


def test_failure_records_its_error_code_and_stops_counting():
    f = transfer().failed(TransferErrorCode("profile_rejected"))
    assert f.transfer_state is FAILED
    assert f.error_code == TransferErrorCode("profile_rejected")
    assert f.admitted_asset_id is None
    assert f.counts is False


@pytest.mark.parametrize("terminal", [ADMITTED, FAILED])
def test_terminal_transfers_cannot_move_again(terminal):
    t = transfer(state=terminal)
    with pytest.raises(IntakeStateConflictError):
        t.admitted()
    with pytest.raises(IntakeStateConflictError):
        t.failed(TransferErrorCode("x"))


def test_the_evidence_must_match_the_state_in_every_combination():
    base = transfer()
    other = AssetId.from_entropy(entropy)
    for bad in (
        {"admitted_asset_id": base.reserved_asset_id},  # RECEIVED but admitted
        {"error_code": TransferErrorCode("x")},  # RECEIVED but errored
        {"transfer_state": ADMITTED},  # ADMITTED without an asset
        {"transfer_state": ADMITTED, "admitted_asset_id": other},  # ADMITTED with the wrong asset
        {"transfer_state": ADMITTED, "admitted_asset_id": base.reserved_asset_id,
         "error_code": TransferErrorCode("x")},  # ADMITTED but errored
        {"transfer_state": FAILED},  # FAILED without a code
        {"transfer_state": FAILED, "error_code": TransferErrorCode("x"),
         "admitted_asset_id": base.reserved_asset_id},  # FAILED but admitted
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            base.evolve(**bad)


def test_a_declared_format_must_suit_the_slot():
    with pytest.raises(ValidationError, match="not allowed"):
        transfer(PackageSlot.DATASET, "onnx")
    with pytest.raises(ValidationError, match="not allowed"):
        transfer(PackageSlot.MODEL, "coco")
    assert transfer(PackageSlot.MODEL, None).declared_format is None
    assert transfer(PackageSlot.REFERENCE, "whatever").declared_format is not None


def test_sizes_are_non_negative_strict_integers_within_the_exact_range():
    assert transfer(size=0).received_byte_size == 0
    for bad in (-1, 2**53, 1.5, True, "1", None):
        with pytest.raises(ValidationError):
            transfer().evolve(received_byte_size=bad)
    assert transfer().evolve(received_byte_size=2**53 - 1).received_byte_size == 2**53 - 1


@pytest.mark.parametrize("name", ["a/b.zip", "a\\b.zip", "x" * 256, " a.zip", "a\x00.zip"])
def test_the_original_filename_is_a_label_not_a_path(name):
    with pytest.raises(ValidationError):
        OriginalFilename(value=name)


def test_an_empty_filename_is_a_valid_label_but_a_path_is_not():
    assert OriginalFilename(value="").value == ""
    assert OriginalFilename(value="data (1).zip").value == "data (1).zip"


@pytest.mark.parametrize("media", ["", "Application/Zip", "application", "application/zip; x=1", "a/b/c", 7])
def test_media_types_must_be_bare_lowercase_type_subtype_or_absent(media):
    with pytest.raises(ValidationError):
        transfer().evolve(media_type=media)
    assert transfer().evolve(media_type=None).media_type is None


def test_a_transfer_is_immutable_closed_and_cannot_be_assembled_unvalidated():
    t = transfer()
    with pytest.raises(ValidationError):
        t.transfer_state = ADMITTED
    with pytest.raises(ValidationError):
        IntakeTransfer(**{**{k: getattr(t, k) for k in IntakeTransfer.model_fields}, "extra": 1})
    with pytest.raises(Exception, match="bypass"):
        IntakeTransfer.model_construct()


def test_ids_are_distinct_types_that_cannot_stand_in_for_each_other():
    t = transfer()
    with pytest.raises(ValidationError):
        t.evolve(reserved_asset_id=t.transfer_id)
    with pytest.raises(ValidationError):
        t.evolve(quarantine_handle=t.transfer_id)


def test_the_field_names_name_their_dimension_not_a_bare_state():
    assert "transfer_state" in IntakeTransfer.model_fields
    assert "state" not in IntakeTransfer.model_fields
