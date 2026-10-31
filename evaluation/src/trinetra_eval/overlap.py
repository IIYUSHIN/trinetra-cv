"""IMP-0410: full graph closure is authoritative over scenario-level assignments."""

from collections.abc import Mapping

from trinetra.domain.enums import WireEnum
from trinetra_eval.closure import isolation_components
from trinetra_eval.hidden import private_document
from trinetra_eval.lineage import EvaluationLineageGraph


class EvaluationPopulation(WireEnum):
    DEVELOPMENT = "DEVELOPMENT"
    CALIBRATION = "CALIBRATION"
    BLIND = "BLIND"
    FINAL = "FINAL"


def cross_population_components(
    graph: EvaluationLineageGraph, assignments: Mapping[str, EvaluationPopulation]
) -> tuple[frozenset[str], ...]:
    """Reject incomplete/unknown assignment maps; report every leaking component."""
    doc = private_document(graph)
    if set(assignments) != set(doc["scenario_ids"]) or any(
        type(p) is not EvaluationPopulation for p in assignments.values()
    ):
        raise ValueError("incomplete or invalid population assignments")
    return tuple(
        c for c in isolation_components(graph) if len({assignments[n] for n in c if n in assignments}) > 1
    )
