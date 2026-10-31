"""IMP-0399/0420: closed visible projection and explicitly guarded serialization entrypoints.

The public facade deliberately has no third-party schema adapter. Such adapters
parse JSON before model validators can sanitize failures. Its private DomainModel
retains the project's frozen, strict, closed schema and JCS conventions.
"""

from __future__ import annotations

from typing import Any, Literal, Never, Self

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra.jcs import canonical_digest, loads_strict
from trinetra_eval.identity import ScenarioId


class VisibleManifestValidationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("invalid visible scenario manifest")


class _VisibleSchema(DomainModel):
    schema_version: Literal[1] = 1
    scenario_id: ScenarioId
    assurance_package_ref: Label
    assurance_package_digest: DigestSha256
    assessment_profile_ref: Label
    visible_reference_refs: tuple[Label, ...]
    declared_access_profile: Label
    resource_hint: Label
    visible_manifest_digest: DigestSha256

    @model_validator(mode="after")
    def check_digest(self) -> _VisibleSchema:
        body = self.to_wire()
        del body["visible_manifest_digest"]
        if str(self.visible_manifest_digest) != canonical_digest(body):
            raise ValueError("visible manifest digest mismatch")
        return self


class VisibleScenarioManifest:
    """Immutable visible value. Use the guarded loaders, never an external JSON adapter."""

    __slots__ = ("__value",)
    __value: _VisibleSchema

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Never:
        # Refuse adaptation before a framework can parse raw JSON outside our guard.
        raise VisibleManifestValidationError()

    def __init__(self, **values: Any) -> None:
        value: _VisibleSchema | None = None
        try:
            value = _VisibleSchema(**values)
        except Exception:  # noqa: BLE001 - rejected private inputs must never enter exported exceptions
            value = None
        if value is None:
            raise VisibleManifestValidationError()
        object.__setattr__(self, "_VisibleScenarioManifest__value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("visible scenario manifest is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("visible scenario manifest is immutable")

    def __repr__(self) -> str:
        return "<visible scenario manifest>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VisibleScenarioManifest) and self.__value == other.__value

    def __hash__(self) -> int:
        return hash(self.__value)

    @classmethod
    def model_validate(cls, obj: object) -> Self:
        if isinstance(obj, cls):
            return obj
        if isinstance(obj, dict):
            return cls(**obj)
        raise VisibleManifestValidationError()

    @classmethod
    def model_validate_strings(cls, obj: object) -> Self:
        """Strict native mapping compatibility; no input coercion."""
        return cls.model_validate(obj)

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray) -> Self:
        try:
            document = loads_strict(bytes(json_data) if isinstance(json_data, bytearray) else json_data)
            if isinstance(document, dict):
                refs = document.get("visible_reference_refs")
                if isinstance(refs, list):
                    document = document | {"visible_reference_refs": tuple(refs)}
                return cls(**document)
        except Exception:  # noqa: BLE001, S110 - rejected inputs are private; never log parser errors
            pass
        raise VisibleManifestValidationError()

    def to_wire(self) -> dict[str, Any]:
        return self.__value.to_wire()

    def canonical_bytes(self) -> bytes:
        return self.__value.canonical_bytes()

    def canonical_digest(self) -> str:
        return self.__value.canonical_digest()

    def model_dump_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")

    def evolve(self, **changes: Any) -> Self:
        values = {name: getattr(self.__value, name) for name in _VisibleSchema.model_fields}
        return type(self)(**(values | changes))

    @property
    def schema_version(self) -> Literal[1]:
        return self.__value.schema_version

    @property
    def scenario_id(self) -> ScenarioId:
        return self.__value.scenario_id

    @property
    def assurance_package_ref(self) -> str:
        return self.__value.assurance_package_ref

    @property
    def assurance_package_digest(self) -> DigestSha256:
        return self.__value.assurance_package_digest

    @property
    def assessment_profile_ref(self) -> str:
        return self.__value.assessment_profile_ref

    @property
    def visible_reference_refs(self) -> tuple[str, ...]:
        return self.__value.visible_reference_refs

    @property
    def declared_access_profile(self) -> str:
        return self.__value.declared_access_profile

    @property
    def resource_hint(self) -> str:
        return self.__value.resource_hint

    @property
    def visible_manifest_digest(self) -> DigestSha256:
        return self.__value.visible_manifest_digest
