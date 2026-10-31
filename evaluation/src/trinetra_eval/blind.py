"""IMP-0413: private Blind membership; no real holdout data is included."""

from typing import Literal

from trinetra_eval.overlap import EvaluationPopulation
from trinetra_eval.populations import PopulationSchema
from trinetra_eval.private import PrivateRecord


class _BlindSchema(PopulationSchema):
    population: Literal[EvaluationPopulation.BLIND]


class BlindPopulationManifest(PrivateRecord):
    __slots__ = ()
    SCHEMA = _BlindSchema
    DIGEST_FIELD = "population_digest"
