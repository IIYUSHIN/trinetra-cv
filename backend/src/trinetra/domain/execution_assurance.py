"""ExecutionAssuranceClass: how far a model package's own code can corrupt what the Runner reports (IMP-0105).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class ExecutionAssuranceClass(WireEnum):
    DATAFLOW_GRAPH = "DATAFLOW_GRAPH"
    SCRIPTED_GRAPH = "SCRIPTED_GRAPH"
    TRUSTED_ARCHITECTURE_WEIGHTS_ONLY = "TRUSTED_ARCHITECTURE_WEIGHTS_ONLY"
    SANDBOXED_CODE_BEARING_PACKAGE = "SANDBOXED_CODE_BEARING_PACKAGE"


__all__ = ["ExecutionAssuranceClass"]
