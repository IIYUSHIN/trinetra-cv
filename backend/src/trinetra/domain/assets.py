"""Typed opaque identifiers: AssetId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.enums import WireEnum
from trinetra.domain.ids import OpaqueId


class AssetId(OpaqueId):
    PREFIX = "asset"
    __slots__ = ()


class AssetKind(WireEnum):
    """What an AssetRecord holds. Wire form is the lowercase value.

    PROVISIONAL MEMBER LIST (ERR-CL-001): the module spec says AssetKind is validated by
    the domain and lists no members in the corpus available to this builder; these are the
    kinds evidenced by the Case asset roles, the TIIR path and ERR-GR-WI-009. Closing the
    list needs an owner REV; adding a member later is additive, removing one is not.
    """

    DATASET = "dataset"
    MODEL = "model"
    CONTRIBUTOR_METADATA = "contributor_metadata"  # added by ERR-GR-WI-009 REV
    REFERENCE = "reference"
    INFERENCE_RECORD = "inference_record"
    ASSESSMENT_PROFILE = "assessment_profile"
    INFERENCE_OUTPUT = "inference_output"
    INFERENCE_RECEIPT = "inference_receipt"


__all__ = ["AssetId", "AssetKind"]
