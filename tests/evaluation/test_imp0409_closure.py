"""Lineage bridges and benign twins cannot be split by edge direction or ordering."""

import itertools

from hypothesis import given
from hypothesis import strategies as st

from trinetra_eval.closure import isolation_components
from trinetra_eval.lineage import EvaluationLineageGraph, LineageEdge


def graph(edges):
    return EvaluationLineageGraph(
        nodes=("a", "b", "c", "d"),
        scenario_ids=(),
        edges=tuple(LineageEdge(source=a, target=b, relation="DERIVED_FROM_SAMPLE") for a, b in edges),
    )


def test_transitive_bridge_and_isolated_node():
    assert isolation_components(graph((("a", "b"), ("c", "b")))) == (frozenset("abc"), frozenset("d"))


@given(st.permutations([("a", "b"), ("c", "b"), ("a", "c")]))
def test_cycle_and_edge_order_do_not_change_components(edges):
    assert isolation_components(graph(edges)) == (frozenset("abc"), frozenset("d"))


def test_empty_and_disconnected_graphs_are_not_lost():
    assert isolation_components(EvaluationLineageGraph(nodes=(), scenario_ids=(), edges=())) == ()
    assert isolation_components(graph(())) == tuple(frozenset(n) for n in "abcd")
    for pairs in itertools.permutations((("a", "b"), ("b", "c"))):
        assert isolation_components(graph(pairs))[0] == frozenset("abc")
