"""Transfer slot and media validation (IMP-0076).

Runs before a single upload byte is read. It checks only what the request itself claims: the slot accepts
the declared format, the media type (when given) is a well-formed claim that the slot's asset kind allows
under the admission profile, and the logical identifier and stream are well-formed. A declared format and
a media type are claims, never detected content: adapters (EIB-06) and the admission sniffing decide what
the bytes really are. The size ceiling comes from the same admission profile, so the application and the
admission step cannot disagree about it.

PROPOSED (ERR-CL-022): a missing media type is admitted as `application/octet-stream` (the profile already
allows it for every kind and lets content sniffing decide); it is stored as absent on the transfer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pydantic import TypeAdapter, ValidationError

from trinetra.application.errors import CommandValidationError
from trinetra.application.intake.commands import ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.slots import DeclaredFormat, slot_contract
from trinetra.domain.asset_admission import DEFAULT_PROFILE, OCTET, AdmissionProfile
from trinetra.domain.asset_record import MediaType
from trinetra.domain.assets import AssetKind
from trinetra.domain.logical_identifier import LogicalIdentifier

_MEDIA_TYPE: Final = TypeAdapter(MediaType)
_LOGICAL_IDENTIFIER: Final = TypeAdapter(LogicalIdentifier)


@dataclass(frozen=True, slots=True)
class ValidatedTransferRequest:
    slot: PackageSlot
    asset_kind: AssetKind
    declared_format: DeclaredFormat | None
    logical_identifier: str | None
    media_type: str | None  # as declared; None when the client gave none
    effective_media_type: str  # what admission will be told
    max_bytes: int


def validate_transfer_request(
    command: ReceiveTransferCommand, profile: AdmissionProfile = DEFAULT_PROFILE
) -> ValidatedTransferRequest:
    contract = slot_contract(command.slot)
    if not contract.allows(command.declared_format):
        raise CommandValidationError(
            f"the {command.slot.value} slot does not accept this declared format",
            details={"field": "declared_format", "slot": command.slot.value},
        )
    identifier = _identifier(command.logical_identifier)
    media = _media(command.media_type)
    rule = profile.rule_for(contract.asset_kind)
    effective = media if media is not None else OCTET
    if effective not in rule.media_types:
        raise CommandValidationError(
            f"the {command.slot.value} slot does not accept this media type",
            details={"field": "media_type", "slot": command.slot.value},
        )
    if not callable(getattr(command.stream, "read", None)):
        raise CommandValidationError("the upload needs a readable byte stream", details={"field": "stream"})
    return ValidatedTransferRequest(
        slot=command.slot,
        asset_kind=contract.asset_kind,
        declared_format=command.declared_format,
        logical_identifier=identifier,
        media_type=media,
        effective_media_type=effective,
        max_bytes=rule.max_bytes,
    )


def _identifier(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _LOGICAL_IDENTIFIER.validate_python(value)
    except ValidationError as exc:
        raise CommandValidationError(
            "the logical identifier is not valid", details={"field": "logical_identifier"}
        ) from exc


def _media(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _MEDIA_TYPE.validate_python(value)
    except ValidationError as exc:
        raise CommandValidationError(
            "the media type is not a well-formed type/subtype", details={"field": "media_type"}
        ) from exc


__all__ = ["ValidatedTransferRequest", "validate_transfer_request"]
