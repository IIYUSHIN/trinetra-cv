"""A shared private ancestor invalidates a cross-population assignment."""

import pytest

from trinetra_eval.lineage import EvaluationLineageGraph, LineageEdge
from trinetra_eval.overlap import EvaluationPopulation as P
from trinetra_eval.overlap import cross_population_components

A = "scn_" + "a" * 32
B = "scn_" + "b" * 32


def graph():
    return EvaluationLineageGraph(
        nodes=(A, B, "parent"),
        scenario_ids=(A, B),
        edges=tuple(LineageEdge(source=n, target="parent", relation="DERIVED_FROM_SAMPLE") for n in (A, B)),
    )


def test_related_derivatives_cannot_cross_populations():
    assert cross_population_components(graph(), {A: P.DEVELOPMENT, B: P.BLIND}) == (
        frozenset({A, B, "parent"}),
    )
    assert cross_population_components(graph(), {A: P.FINAL, B: P.FINAL}) == ()


@pytest.mark.parametrize(
    "assignment", [{}, {A: P.BLIND}, {A: P.BLIND, B: "FINAL"}, {A: P.BLIND, B: P.FINAL, "unknown": P.FINAL}]
)
def test_partial_unknown_and_untyped_assignments_fail_closed(assignment):
    with pytest.raises(ValueError):
        cross_population_components(graph(), assignment)


def test_distinct_components_can_use_distinct_populations():
    g = EvaluationLineageGraph(nodes=(A, B), scenario_ids=(A, B), edges=())
    assert cross_population_components(g, {A: P.BLIND, B: P.FINAL}) == ()
