"""Executable import-boundary contract for the backend package (IMP-0004).

Standard library only. The rules below are the Bible's dependency law
(FROZEN_BIBLE lines 131-158: domain pure; application orchestrates;
infrastructure implements ports; API is transport; Runner separate;
evaluation may call production but production never imports evaluation)
plus the offline/no-telemetry/hostile-input constraints (lines 13846, 14001-14026).

Anything the Bible does not constrain (imports between the peer layers
adapters/methods/execution/tiir/assurance/infrastructure) is deliberately NOT
restricted here beyond "no cycles"; tightening it needs an ADR, not a guess.

`check_tree` is fail-closed: unparsable files, unknown layers, unlisted
third-party imports and dynamic imports are violations, never silently skipped.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "trinetra"
LAYERS = frozenset({
    "domain", "application", "infrastructure", "api", "adapters",
    "methods", "execution", "tiir", "assurance",
})
ALL_LAYERS = LAYERS

# Modules that live directly in the package root. Adding one is a deliberate
# act: it must be listed here (BOOT-006 otherwise) and, if package code may
# import it, also in ROOT_IMPORTABLE (ROOT-002 otherwise). `boundaries` is
# tooling and is not importable from package code.
ROOT_MODULES = frozenset({"errors", "boundaries", "settings", "timeutil", "digest", "jcs", "structured_log"})
ROOT_IMPORTABLE = frozenset({"errors", "settings", "timeutil", "digest", "jcs", "structured_log"})
ROOT_ATTRIBUTES = frozenset({"__version__"})  # `from trinetra import __version__`

# Production code must never import these top-level packages (evaluation holds
# hidden truth; runner is hostile-side code).
FORBIDDEN_TOPLEVEL = frozenset({"trinetra_eval", "runner"})

# third-party top-level import name -> layers allowed to import it.
THIRD_PARTY: dict[str, frozenset[str]] = {
    "pydantic": ALL_LAYERS,
    "sqlalchemy": frozenset({"infrastructure"}),
    "alembic": frozenset({"infrastructure"}),
    "fastapi": frozenset({"api"}),
    "starlette": frozenset({"api"}),
    "uvicorn": frozenset({"api"}),
    "cbor2": frozenset({"tiir", "infrastructure"}),
    "cryptography": frozenset({"tiir", "infrastructure"}),
    "numpy": frozenset({"adapters", "methods"}),
    "PIL": frozenset({"adapters", "methods"}),
    # torch / torchvision / onnxruntime: intentionally absent. They run only in
    # the hostile Runner, never in the Core package.
}

NOBODY: frozenset[str] = frozenset()
# stdlib modules that are restricted. Anything not listed is unrestricted.
STDLIB_ALLOWED_IN: dict[str, frozenset[str]] = {
    # unsafe deserialisation / native code / network: product is offline and
    # parses hostile bytes.
    "pickle": NOBODY, "marshal": NOBODY, "shelve": NOBODY, "ctypes": NOBODY,
    "socket": NOBODY, "ssl": NOBODY, "urllib": NOBODY, "ftplib": NOBODY,
    "smtplib": NOBODY, "telnetlib": NOBODY, "xmlrpc": NOBODY, "imaplib": NOBODY,
    "poplib": NOBODY, "nntplib": NOBODY, "socketserver": NOBODY,
    "webbrowser": NOBODY, "ensurepip": NOBODY,
    "http": frozenset({"api"}),  # http.HTTPStatus etc.
    "subprocess": frozenset({"execution"}),
    "multiprocessing": frozenset({"execution"}),
    "sqlite3": frozenset({"infrastructure"}),
}
# stdlib modules the pure domain layer must not touch (I/O, process, threads).
DOMAIN_FORBIDDEN_STDLIB = frozenset({
    "os", "shutil", "tempfile", "importlib", "threading", "asyncio", "signal",
})

DYNAMIC_CALLS = frozenset({"__import__", "import_module", "eval", "exec", "compile"})


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _module_for(rel: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(module parts, package parts used to resolve relative imports)."""
    parts = rel.with_suffix("").parts
    is_init = parts[-1] == "__init__"
    module = (PACKAGE, *(parts[:-1] if is_init else parts))
    package = module if is_init else module[:-1]
    return module, package


def _imports(tree: ast.AST, package: tuple[str, ...]):
    """Yield (lineno, absolute dotted name) or (lineno, None) when unresolvable."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 1:
                    yield node.lineno, None
                    continue
                base = ".".join((*package[:keep], *([node.module] if node.module else [])))
            else:
                base = node.module or ""
            yield node.lineno, base
            if any(alias.name == "*" for alias in node.names):
                yield node.lineno, "*"
            if base == PACKAGE:  # `from trinetra import domain` imports a layer
                for alias in node.names:
                    yield node.lineno, f"{PACKAGE}.{alias.name}"


def _dynamic_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if (name == "compile" and isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name) and func.value.id == "re"):
                continue  # re.compile is regex compilation, not code compilation
            if name in DYNAMIC_CALLS:
                yield node.lineno, name


def _find_cycle(edges: set[tuple[str, str]]) -> list[str] | None:
    graph: dict[str, set[str]] = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt):], nxt]
            if state.get(nxt) is None and (found := visit(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for start in sorted(graph):
        if state.get(start) is None and (found := visit(start)):
            return found
    return None


def check_tree(src_root: Path) -> list[Violation]:
    """Audit `src_root/trinetra`; return sorted violations (empty = conformant)."""
    pkg_root = src_root / PACKAGE
    if pkg_root.is_symlink() or not pkg_root.is_dir():
        return [Violation(str(pkg_root), 0, "BOOT-001", "package directory missing or symlink")]
    out: list[Violation] = []
    edges: set[tuple[str, str]] = set()

    for path in sorted(pkg_root.rglob("*")):
        rel = path.relative_to(pkg_root)
        shown = f"{PACKAGE}/{rel.as_posix()}"
        if "__pycache__" in rel.parts:
            continue
        if path.is_symlink():
            out.append(Violation(shown, 0, "BOOT-002", "symlink inside package"))
            continue
        if path.is_dir():
            if len(rel.parts) == 1 and rel.name not in LAYERS:
                out.append(Violation(shown, 0, "BOOT-003", f"unknown layer {rel.name!r}"))
            if not (path / "__init__.py").is_file():
                out.append(Violation(shown, 0, "BOOT-004", "package without __init__.py"))
            continue
        if path.suffix != ".py":
            if rel.as_posix() != "py.typed":
                out.append(Violation(shown, 0, "BOOT-005", "unexpected non-Python file"))
            continue

        _, package = _module_for(rel)
        # Files directly in the package root have layer None; files inside a
        # subdirectory belong to that directory's layer.
        layer = rel.parts[0] if len(rel.parts) > 1 else None
        if layer is None and rel.name != "__init__.py" and rel.stem not in ROOT_MODULES:
            out.append(Violation(shown, 0, "BOOT-006", f"unregistered root module {rel.stem!r}"))
        if layer is not None and layer not in LAYERS:
            continue  # already reported as an unknown layer (BOOT-003)
        try:
            tree = ast.parse(path.read_bytes().decode("utf-8"), filename=shown)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            out.append(Violation(shown, 0, "PARSE-001", f"cannot analyse: {type(exc).__name__}"))
            continue

        for lineno, name in _dynamic_calls(tree):
            out.append(Violation(shown, lineno, "DYN-001", f"dynamic code/import via {name}()"))
        for lineno, name in _imports(tree, package):
            if name is None:
                out.append(Violation(shown, lineno, "PARSE-002", "relative import escapes package"))
                continue
            if name == "*":
                out.append(Violation(shown, lineno, "WILD-001", "wildcard import hides dependencies"))
                continue
            out.extend(_classify(shown, lineno, name, layer, edges))

    cycle = _find_cycle(edges)
    if cycle:
        out.append(Violation(PACKAGE, 0, "CYCLE-001", " -> ".join(cycle)))
    return sorted(out)


def _classify(shown, lineno, name, layer, edges) -> list[Violation]:
    top = name.split(".")[0]
    bad = lambda rule, msg: [Violation(shown, lineno, rule, msg)]
    if top in FORBIDDEN_TOPLEVEL:
        return bad("EVAL-001", f"production code must not import {top!r}")
    if top == PACKAGE:
        parts = name.split(".")
        target = parts[1] if len(parts) > 1 else None
        if target is None or target == layer:
            return []
        if target in ROOT_MODULES or target in ROOT_ATTRIBUTES:
            if target in ROOT_IMPORTABLE or target in ROOT_ATTRIBUTES:
                return []
            return bad("ROOT-002", f"root module {target!r} is not importable from package code")
        if target not in LAYERS:
            return bad("BOOT-003", f"import of unknown layer {target!r}")
        if layer is None:
            return bad("ROOT-001", f"package root must not import trinetra.{target}")
        edges.add((layer, target))
        if layer == "domain":
            return bad("DOM-001", f"domain must be pure; imports trinetra.{target}")
        if layer == "application" and target != "domain":
            return bad("APP-001", f"application may import only domain; imports {target}")
        if target == "api":
            return bad("API-001", f"only api may import trinetra.api; {layer} does")
        return []
    if top in sys.stdlib_module_names:
        if top in STDLIB_ALLOWED_IN and layer not in STDLIB_ALLOWED_IN[top]:
            return bad("STD-001", f"stdlib {top!r} not allowed in {layer or 'package root'}")
        if layer == "domain" and top in DOMAIN_FORBIDDEN_STDLIB:
            return bad("DOM-002", f"domain must be pure; stdlib {top!r} forbidden")
        return []
    allowed = THIRD_PARTY.get(top)
    if allowed is None:
        return bad("DEP-001", f"unlisted or forbidden third-party import {top!r}")
    if layer not in allowed:
        return bad("DEP-002", f"{top!r} not allowed in {layer or 'package root'}")
    return []
