"""IMP-0414: private Final membership; no real holdout data is included."""

from typing import Literal

from trinetra_eval.overlap import EvaluationPopulation
from trinetra_eval.populations import PopulationSchema
from trinetra_eval.private import PrivateRecord


class _FinalSchema(PopulationSchema):
    population: Literal[EvaluationPopulation.FINAL]


class FinalPopulationManifest(PrivateRecord):
    __slots__ = ()
    SCHEMA = _FinalSchema
    DIGEST_FIELD = "population_digest"
