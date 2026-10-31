"""IMP-0412: private Calibration membership; no real holdout data is included."""

from typing import Literal

from trinetra_eval.hidden import PrivateRecord
from trinetra_eval.overlap import EvaluationPopulation
from trinetra_eval.populations import PopulationSchema


class _CalibrationSchema(PopulationSchema):
    population: Literal[EvaluationPopulation.CALIBRATION]


class CalibrationPopulationManifest(PrivateRecord):
    __slots__ = ()
    SCHEMA = _CalibrationSchema
    DIGEST_FIELD = "population_digest"
