"""ScopedMetric value object (IMP-0032).

Source contract (ScopedMetricDto): `metric_id, label, value, unit, method_ref, scope_refs[],
reference_ref?, calibration_ref?, interpretation_label?, limitations[]`; "Forbidden: trust_score
unless a future domain decision defines exact semantics -- which baseline does not".

PROPOSED engineering decisions (no source fixes them): `value` is a finite number (bool, NaN, inf
rejected; ints accepted and kept as ints so counts stay exact); `unit` is a canonical token;
`method_ref`/`reference_ref`/`calibration_ref` are ReferenceIds; `limitations` are LimitationIds
(<= 64, unique) -- the Limitation objects themselves live elsewhere; scope_refs >= 1 so a metric
can never float free of what it measures. A metric is a measurement with provenance, never a
verdict: no field here can express pass/fail, score or trust.
"""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import AfterValidator, Field

from trinetra.domain.base import DomainModel
from trinetra.domain.limitations import MAX_SCOPE_REFS, LimitationId, ScopeRefs
from trinetra.domain.metrics import MetricId
from trinetra.domain.references import ReferenceId
from trinetra.domain.text import Label
from trinetra.domain.tokens import CanonicalToken

MAX_LIMITATIONS = MAX_SCOPE_REFS
MAX_SAFE_INT = 2**53 - 1


class MetricUnit(CanonicalToken):
    KIND = "metric_unit"
    __slots__ = ()


def _finite(value: int | float) -> int | float:
    if isinstance(value, bool):
        raise ValueError("metric value must be a number, not a bool")  # noqa: TRY004 - pydantic needs ValueError
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metric value must be finite")
    if isinstance(value, int) and abs(value) > MAX_SAFE_INT:
        raise ValueError("metric integer value is beyond I-JSON range")
    return value


def _unique_ids(ids: tuple[LimitationId, ...]) -> tuple[LimitationId, ...]:
    if len(set(ids)) != len(ids):
        raise ValueError("limitations must not contain duplicates")
    return ids


MetricValue = Annotated[int | float, AfterValidator(_finite)]
LimitationIds = Annotated[
    tuple[LimitationId, ...], Field(max_length=MAX_LIMITATIONS), AfterValidator(_unique_ids)
]


class ScopedMetric(DomainModel):
    metric_id: MetricId
    label: Label
    value: MetricValue
    unit: MetricUnit
    method_ref: ReferenceId
    scope_refs: ScopeRefs
    reference_ref: ReferenceId | None = None
    calibration_ref: ReferenceId | None = None
    interpretation_label: Label | None = None
    limitations: LimitationIds = ()


__all__ = ["MAX_LIMITATIONS", "MetricUnit", "MetricValue", "ScopedMetric"]
