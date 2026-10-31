"""IMP-0076 tests: slot, format and media claims are validated before any byte is read."""

from __future__ import annotations

import dataclasses

import pytest

from intake_support import ScriptedStream
from trinetra.application.errors import CommandValidationError
from trinetra.application.intake.commands import ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.slots import SLOT_CONTRACTS, DeclaredFormat
from trinetra.application.intake.validation import validate_transfer_request
from trinetra.domain.asset_admission import DEFAULT_PROFILE, OCTET, AdmissionProfile, KindRule
from trinetra.domain.assets import AssetKind
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId

GIB = 1024**3


def command(slot=PackageSlot.DATASET, fmt="coco", media="application/zip", ident=None, stream=None):
    return ReceiveTransferCommand(
        intake_id=IntakeId.from_entropy(lambda n: b"\x01" * n),
        command_id=CommandId.from_entropy(lambda n: b"\x02" * n),
        slot=slot,
        declared_format=DeclaredFormat(fmt) if fmt else None,
        logical_identifier=ident,
        filename=OriginalFilename(value="upload.bin"),
        media_type=media,
        stream=stream if stream is not None else ScriptedStream(),
    )


def field_of(exc):
    return exc.value.details["field"]


def test_a_well_formed_dataset_upload_is_accepted_with_the_profile_ceiling():
    request = validate_transfer_request(command())
    assert request.slot is PackageSlot.DATASET
    assert request.asset_kind is AssetKind.DATASET
    assert request.media_type == request.effective_media_type == "application/zip"
    assert request.max_bytes == 8 * GIB


def test_a_missing_media_type_is_admitted_as_octet_stream_but_stored_as_absent():
    request = validate_transfer_request(command(media=None))
    assert request.media_type is None
    assert request.effective_media_type == OCTET


@pytest.mark.parametrize(("slot", "fmt"), [
    (PackageSlot.DATASET, "onnx"), (PackageSlot.DATASET, "json"),
    (PackageSlot.MODEL, "coco"), (PackageSlot.MODEL, "json"),
    (PackageSlot.CONTRIBUTOR_METADATA, "coco"), (PackageSlot.ASSESSMENT_PROFILE, "onnx"),
])  # fmt: skip
def test_a_declared_format_the_slot_does_not_accept_is_rejected(slot, fmt):
    with pytest.raises(CommandValidationError) as exc:
        validate_transfer_request(command(slot=slot, fmt=fmt, media="application/json"))
    assert exc.value.details == {"field": "declared_format", "slot": slot.value}


def test_every_slot_accepts_exactly_its_contract_formats_and_none():
    for slot, contract in SLOT_CONTRACTS.items():
        profile_types = DEFAULT_PROFILE.rule_for(contract.asset_kind).media_types
        media = min(profile_types)
        validate_transfer_request(command(slot=slot, fmt=None, media=media))
        for declared in contract.allowed_formats or (DeclaredFormat("anything"),):
            validate_transfer_request(command(slot=slot, fmt=declared.text, media=media))
        if contract.allowed_formats is not None:
            with pytest.raises(CommandValidationError):
                validate_transfer_request(command(slot=slot, fmt="notaformat", media=media))


@pytest.mark.parametrize(("slot", "fmt", "media"), [
    (PackageSlot.MODEL, "onnx", "image/png"),
    (PackageSlot.MODEL, "onnx", "application/json"),
    (PackageSlot.INFERENCE_RECORDS, None, "application/zip"),
    (PackageSlot.ASSESSMENT_PROFILE, "json", "text/csv"),
    (PackageSlot.DATASET, "coco", "video/mp4"),
])  # fmt: skip
def test_a_media_type_the_slot_kind_does_not_allow_is_rejected(slot, fmt, media):
    with pytest.raises(CommandValidationError) as exc:
        validate_transfer_request(command(slot=slot, fmt=fmt, media=media))
    assert field_of(exc) == "media_type"


@pytest.mark.parametrize("media", [
    "", "zip", "Application/Zip", "application/zip; charset=utf-8", "application/", "/zip",
    "application/zip ", "a" * 80 + "/zip", "application/z\nip",
])  # fmt: skip
def test_a_malformed_media_type_is_rejected(media):
    with pytest.raises(CommandValidationError) as exc:
        validate_transfer_request(command(media=media))
    assert field_of(exc) == "media_type"
    assert "well-formed" in str(exc.value)


@pytest.mark.parametrize("ident", ["", " lead", "trail ", "two  spaces", "x" * 129, "é"])
def test_a_malformed_logical_identifier_is_rejected(ident):
    with pytest.raises(CommandValidationError) as exc:
        validate_transfer_request(command(ident=ident))
    assert field_of(exc) == "logical_identifier"


def test_a_good_or_absent_logical_identifier_passes_through_unchanged():
    assert validate_transfer_request(command(ident="Coco val 2017")).logical_identifier == "Coco val 2017"
    assert validate_transfer_request(command(ident=None)).logical_identifier is None


@pytest.mark.parametrize("stream", [object(), None, "bytes", 7, b"raw"])
def test_the_upload_needs_a_readable_stream(stream):
    cmd = dataclasses.replace(command(), stream=stream)
    with pytest.raises(CommandValidationError) as exc:
        validate_transfer_request(cmd)
    assert field_of(exc) == "stream"


def test_validation_never_reads_a_byte():
    stream = ScriptedStream()
    validate_transfer_request(command(stream=stream))
    with pytest.raises(CommandValidationError):
        validate_transfer_request(command(fmt="onnx", stream=stream))
    assert stream.reads == 0


def test_the_ceiling_and_media_types_come_from_the_injected_profile():
    small = AdmissionProfile(tuple(
        (kind, KindRule(frozenset({"application/zip", OCTET}), 1234)) for kind in AssetKind
    ))  # fmt: skip
    assert validate_transfer_request(command(), small).max_bytes == 1234
    with pytest.raises(CommandValidationError):
        validate_transfer_request(command(media="application/json"), small)


def test_each_slot_gets_the_ceiling_of_its_own_asset_kind():
    for slot, contract in SLOT_CONTRACTS.items():
        media = min(DEFAULT_PROFILE.rule_for(contract.asset_kind).media_types)
        request = validate_transfer_request(command(slot=slot, fmt=None, media=media))
        assert request.max_bytes == DEFAULT_PROFILE.rule_for(contract.asset_kind).max_bytes
        assert request.asset_kind is contract.asset_kind
