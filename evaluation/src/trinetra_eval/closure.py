"""IMP-0409: undirected connected components before any population assignment."""

from trinetra_eval.hidden import private_document
from trinetra_eval.lineage import EvaluationLineageGraph


def isolation_components(graph: EvaluationLineageGraph) -> tuple[frozenset[str], ...]:
    """Include isolated nodes and all intermediate ancestors; cycle-safe iterative traversal."""
    doc = private_document(graph)
    adjacency: dict[str, set[str]] = {n: set() for n in doc["nodes"]}
    for edge in doc["edges"]:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    remaining = set(adjacency)
    components: list[frozenset[str]] = []
    while remaining:
        pending = [min(remaining)]
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            pending.extend(adjacency[node] - seen)
        remaining -= seen
        components.append(frozenset(seen))
    return tuple(components)
