"""IMP-0396: fail-closed source audit, independent of production decision logic."""

from __future__ import annotations

import ast


def audit_source(source: str, *, production: bool) -> tuple[int, ...]:
    """Return violating line numbers; never execute the supplied source.

    Dynamic imports/code and wildcard imports require review rather than a guessed
    dependency. Evaluation may use primitives, but never the scorer's subject logic.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return (0,)
    bad: set[int] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [
                node.module or "",
                *(a.name for a in node.names),
                *((node.module or "") + "." + a.name for a in node.names),
            ]
            if any(a.name == "*" for a in node.names):
                bad.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id in {"__import__", "eval", "exec", "compile"}:
            bad.add(node.lineno)
        elif isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "re"
                and f.attr == "compile"
            ):
                continue
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in {"__import__", "import_module", "eval", "exec", "compile"}:
                bad.add(node.lineno)
        for name in names:
            parts = name.split(".")
            if name in {"importlib", "import_module", "importlib.import_module"}:
                bad.add(getattr(node, "lineno", 0))
            if production and parts[0] == "trinetra_eval":
                bad.add(getattr(node, "lineno", 0))
            if not production and name.startswith(("trinetra.assurance", "trinetra.application")):
                bad.add(getattr(node, "lineno", 0))
    return tuple(sorted(bad))
