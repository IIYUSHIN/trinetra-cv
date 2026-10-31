"""IMP-0400: opaque private truth, with explicit evaluator-only declassification.

Schemas are transient parsers. Only immutable canonical bytes are retained; no
Pydantic model, mapping, dataclass fields or object dictionary is exposed. This
is an accidental-export barrier, not isolation from hostile Python in this process.
Physical assessed-runtime separation is additionally required by LAW-IMP-200.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra.jcs import canonical_digest, canonicalize, loads_strict
from trinetra_eval.identity import ScenarioId


class PrivateSchema(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )


class PrivateRecord:
    """No implicit serialization. Private bytes must be requested deliberately."""

    __slots__ = ("__payload",)
    SCHEMA: ClassVar[type[BaseModel]]
    DIGEST_FIELD: ClassVar[str | None] = None
    __payload: bytes

    def __init__(self, **values: Any) -> None:
        payload: bytes | None = None
        try:
            parsed = self.SCHEMA(**values).model_dump(mode="json")
            if self.DIGEST_FIELD:
                declared = parsed.pop(self.DIGEST_FIELD)
                if declared != canonical_digest(parsed):
                    payload = b""
                parsed[self.DIGEST_FIELD] = declared
            if payload != b"":
                payload = canonicalize(parsed)
        except Exception:  # noqa: BLE001 - private validation never propagates input-bearing errors
            payload = None
        if not payload:
            raise ValueError("invalid private evaluation record")
        object.__setattr__(self, "_PrivateRecord__payload", payload)

    def evaluator_private_bytes(self) -> bytes:
        """Private authority operation; never call from visible export code."""
        return self.__payload

    def __repr__(self) -> str:
        return "<private evaluation record>"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("private evaluation record is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("private evaluation record cannot be serialized")


class _TruthSchema(PrivateSchema):
    schema_version: Literal[1]
    scenario_id: ScenarioId
    evaluation_family: str
    scenario_family: str
    cause_class: str
    mutation_truth: dict[str, JsonValue]
    behavioural_truth: dict[str, JsonValue]
    generation_truth: dict[str, JsonValue]
    affected_entity_truth: tuple[str, ...]
    benign_entity_truth: tuple[str, ...]
    expected_method_targets: tuple[str, ...]
    expected_claims: tuple[dict[str, JsonValue], ...]
    expected_coverage: tuple[dict[str, JsonValue], ...]
    expected_policy: dict[str, JsonValue]
    expected_scope: dict[str, JsonValue]
    expected_lineage: dict[str, JsonValue]
    expected_capsule: dict[str, JsonValue]
    evaluation_isolation_group_id: str
    generation_seed_ref: str
    reproducibility_record_ref: str
    created_at: UtcTimestamp
    truth_digest: DigestSha256


class HiddenTruthRecord(PrivateRecord):
    __slots__ = ()
    SCHEMA = _TruthSchema
    DIGEST_FIELD = "truth_digest"


def private_document(record: PrivateRecord) -> dict[str, Any]:
    """Explicit private read for evaluator composition/sealing, never a visible export."""
    value = loads_strict(record.evaluator_private_bytes())
    if not isinstance(value, dict):
        raise TypeError("private document must be an object")
    return value
