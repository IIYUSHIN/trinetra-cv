"""IMP-0399: closed visible projection; no truth, split, generator or oracle fields."""

from typing import Any, Literal, Self

from pydantic import GetCoreSchemaHandler, ValidatorFunctionWrapHandler, model_validator
from pydantic_core import core_schema

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra.jcs import canonical_digest
from trinetra_eval.identity import ScenarioId


class VisibleManifestValidationError(RuntimeError):
    """Sanitized boundary error; Pydantic must not re-wrap and attach rejected input."""

    def __init__(self) -> None:
        super().__init__("invalid visible scenario manifest")


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

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source)

        def safe(value: Any, inner: ValidatorFunctionWrapHandler, info: core_schema.ValidationInfo) -> Any:
            if (
                info.mode == "json"
                and isinstance(value, dict)
                and isinstance(value.get("visible_reference_refs"), list)
            ):
                value = value | {"visible_reference_refs": tuple(value["visible_reference_refs"])}
            try:
                return inner(value)
            except ValueError:
                pass
            raise VisibleManifestValidationError()

        return core_schema.with_info_wrap_validator_function(safe, schema)

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs: Any) -> Self:
        try:
            return super().model_validate_json(json_data, **kwargs)
        except ValueError:
            pass
        raise VisibleManifestValidationError()

    @model_validator(mode="after")
    def check_digest(self) -> "VisibleScenarioManifest":
        body = self.to_wire()
        del body["visible_manifest_digest"]
        if str(self.visible_manifest_digest) != canonical_digest(body):
            raise ValueError("visible manifest digest mismatch")
        return self
