"""Incomplete leakage graphs fail closed; common software is not automatically lineage."""

import pytest

from trinetra_eval.hidden import private_document
from trinetra_eval.lineage import EvaluationLineageGraph, LineageEdge

A = "scn_" + "a" * 32
B = "scn_" + "b" * 32


def test_explicit_derived_lineage_is_retained_privately():
    edge = LineageEdge(source=A, target=B, relation="MATCHED_BENIGN_TWIN_OF")
    graph = EvaluationLineageGraph(nodes=(A, B), scenario_ids=(A, B), edges=(edge,))
    assert private_document(graph)["edges"][0]["relation"] == "MATCHED_BENIGN_TWIN_OF"
    assert A not in repr(graph)


@pytest.mark.parametrize("change", [{"nodes": (A, A)}, {"nodes": (A,)}, {"scenario_ids": (B,)}])
def test_missing_nodes_and_duplicate_nodes_rejected(change):
    with pytest.raises(ValueError):
        EvaluationLineageGraph(
            **(
                {
                    "nodes": (A,),
                    "scenario_ids": (A,),
                    "edges": (LineageEdge(source=A, target=B, relation="DERIVED_FROM_SAMPLE"),),
                }
                | change
            )
        )


def test_empty_graph_is_valid_and_unknown_edge_types_are_not():
    assert private_document(EvaluationLineageGraph(nodes=(), scenario_ids=(), edges=()))["nodes"] == []
    with pytest.raises(ValueError):
        LineageEdge(source=A, target=B, relation="USES_PUBLIC_ARCHITECTURE")
