"""IMP-0399: closed visible projection; no truth, split, generator or oracle fields."""

from typing import Any, Literal, Self

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra.jcs import canonical_digest
from trinetra_eval.identity import ScenarioId


class VisibleScenarioManifest(DomainModel):
    schema_version: Literal[1] = 1
    scenario_id: ScenarioId
    assurance_package_ref: Label
    assurance_package_digest: DigestSha256
    assessment_profile_ref: Label
    visible_reference_refs: tuple[Label, ...]
    declared_access_profile: Label
    resource_hint: Label
    visible_manifest_digest: DigestSha256

    def __init__(self, **values: Any) -> None:
        valid = False
        try:
            super().__init__(**values)
            valid = True
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("invalid visible scenario manifest")

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs: Any) -> Self:
        try:
            return super().model_validate_json(json_data, **kwargs)
        except ValueError:
            pass
        raise ValueError("invalid visible scenario manifest")

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, **kwargs)
        except ValueError:
            pass
        raise ValueError("invalid visible scenario manifest")

    @model_validator(mode="after")
    def check_digest(self) -> "VisibleScenarioManifest":
        body = self.to_wire()
        del body["visible_manifest_digest"]
        if str(self.visible_manifest_digest) != canonical_digest(body):
            raise ValueError("visible manifest digest mismatch")
        return self
