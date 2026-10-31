"""IMP-0406: preserve obligation applicability and access separately from coverage."""

from trinetra.domain.coverage import CoverageStatus
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema


class _CoverageSchema(PrivateSchema):
    coverage_obligation_id: Label
    applicable: bool
    capability_available: bool
    reference_available: bool
    expected_status: CoverageStatus
    reason: Label


class OracleCoverageExpectation(PrivateRecord):
    __slots__ = ()
    SCHEMA = _CoverageSchema
