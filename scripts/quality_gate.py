#!/usr/bin/env python3
"""Local / CI quality-gate runner (IMP-0014).

Runs the repository's quality gates in a fixed order and reports each as
``PASS``, ``FAIL`` or ``NOT_RUN``. Honesty rules (PROPOSED engineering
decisions; the Bible fixes only "CI/local quality-gate scripts"):

* A gate whose tool is not installed is ``NOT_RUN`` - never ``PASS``.
* Exit code ``0`` only when every selected gate is ``PASS``; ``1`` if any gate
  ``FAIL``; ``2`` if nothing failed but a gate was ``NOT_RUN``; ``64`` for bad
  usage. ``complete`` in the report is true only when *all* gates ran and passed
  (a ``--only``/``--skip`` selection is reported as incomplete).
* Offline by construction: no gate downloads anything (``UV_OFFLINE=1``), the
  network test opt-in variable is removed from the child environment unless
  ``--network`` is given, and no telemetry is sent.
* This tool reports author-side gate results. It is NOT independent review and
  never states that a packet is ACCEPTED.

Standard library plus (optionally) the ``trinetra`` package for the two
in-process gates; without the package those gates are ``NOT_RUN``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

PASS: Final = "PASS"  # noqa: S105 - status word, not a password
FAIL: Final = "FAIL"
NOT_RUN: Final = "NOT_RUN"
SKIPPED: Final = "SKIPPED"
EXIT_OK: Final = 0
EXIT_FAIL: Final = 1
EXIT_NOT_RUN: Final = 2
EXIT_USAGE: Final = 64
REPORT_VERSION: Final = 1
DEFAULT_TIMEOUT_S: Final = 1800
OUTPUT_TAIL_LINES: Final = 40
OUTPUT_TAIL_CHARS: Final = 4000
ENV_PREFIX: Final = "TRINETRA_"
NETWORK_ENV: Final = "TRINETRA_NETWORK_TESTS"
REPO_ROOT: Final = Path(__file__).resolve().parents[1]
SRC_DIR: Final = REPO_ROOT / "backend" / "src"


@dataclass(frozen=True)
class Gate:
    """One gate: an external command or an in-process check."""

    name: str
    argv: tuple[str, ...] = ()
    python_module: str | None = None  # tool run as `python -m <module>`
    executable: str | None = None  # tool looked up on PATH
    in_process: Callable[[Path], tuple[str, str]] | None = None


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    detail: str = ""
    duration_s: float = 0.0
    exit_code: int | None = None


def _redact(text: str) -> str:
    try:
        from trinetra.security import DEFAULT_REDACTOR  # noqa: PLC0415 - optional at runtime
    except ImportError:
        return "<output withheld: redactor unavailable>"
    return DEFAULT_REDACTOR.redact_text(text)


def clip_output(text: str) -> str:
    """Redacted tail of command output, bounded in lines and characters."""
    tail = "\n".join(text.splitlines()[-OUTPUT_TAIL_LINES:])
    return _redact(tail)[-OUTPUT_TAIL_CHARS:]


def child_environment(base: Mapping[str, str], *, network: bool) -> dict[str, str]:
    """Environment for gate subprocesses: offline uv, no stray TRINETRA_* settings."""
    env = {k: v for k, v in base.items() if not k.startswith(ENV_PREFIX)}
    if network and base.get(NETWORK_ENV) == "1":
        env[NETWORK_ENV] = "1"
    env["UV_OFFLINE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# ------------------------------------------------------------ in-process gates
def _import_trinetra() -> None:
    if importlib.util.find_spec("trinetra") is None and str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))


def gate_boundaries(root: Path) -> tuple[str, str]:
    """Import-boundary audit of the real source tree (IMP-0004/0005 rules)."""
    _import_trinetra()
    try:
        from trinetra.boundaries import check_tree  # noqa: PLC0415 - optional at runtime
    except ImportError as exc:
        return NOT_RUN, f"trinetra package not importable: {type(exc).__name__}"
    violations = check_tree(root / "backend" / "src")
    if violations:
        return FAIL, "\n".join(str(v) for v in violations[:OUTPUT_TAIL_LINES])
    return PASS, "no import-boundary violations"


def _git_files(root: Path) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        capture_output=True, check=True, timeout=120,
    )
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def gate_repo_secrets(root: Path) -> tuple[str, str]:
    """No forbidden paths and no high-confidence secret shapes in repo files."""
    _import_trinetra()
    try:
        from trinetra.security import (  # noqa: PLC0415 - optional at runtime
            HIGH_CONFIDENCE_RULES,
            find_secret_patterns,
            is_forbidden_repo_path,
        )
    except ImportError as exc:
        return NOT_RUN, f"trinetra package not importable: {type(exc).__name__}"
    try:
        files = _git_files(root)
    except (OSError, subprocess.SubprocessError):
        return NOT_RUN, "not a git work tree or git unavailable"
    problems: list[str] = []
    for rel in files:
        if is_forbidden_repo_path(rel):
            problems.append(f"{rel}: forbidden path")
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: out of scope for the text scan
        # report rule + location only; never echo the matched text
        hits = find_secret_patterns(text, HIGH_CONFIDENCE_RULES)
        problems += [f"{rel}@{off}: {rule}" for rule, off in hits]
    if problems:
        return FAIL, "\n".join(problems[:OUTPUT_TAIL_LINES])
    return PASS, f"{len(files)} files scanned"


PY = sys.executable
GATES: Final[tuple[Gate, ...]] = (
    Gate("ruff", (PY, "-m", "ruff", "check", "."), python_module="ruff"),
    Gate("mypy", (PY, "-m", "mypy"), python_module="mypy"),
    Gate("boundaries", in_process=gate_boundaries),
    Gate("repo-secrets", in_process=gate_repo_secrets),
    Gate("uv-lock", ("uv", "lock", "--check"), executable="uv"),
    Gate("diff-check", ("git", "diff", "--check", "HEAD"), executable="git"),
    Gate("pytest", (PY, "-m", "pytest", "-q"), python_module="pytest"),
)
GATE_NAMES: Final = tuple(g.name for g in GATES)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_gate(
    gate: Gate,
    root: Path,
    env: Mapping[str, str],
    *,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
) -> GateResult:
    """Run one gate. Never raises for tool problems: they become NOT_RUN / FAIL."""
    started = clock()

    def done(status: str, detail: str, code: int | None = None) -> GateResult:
        return GateResult(gate.name, status, detail, round(clock() - started, 3), code)

    if gate.in_process is not None:
        try:
            status, detail = gate.in_process(root)
        except Exception as exc:  # noqa: BLE001 - a crashing gate is a failed gate, not a pass
            return done(FAIL, f"gate crashed: {type(exc).__name__}")
        return done(status, clip_output(detail))
    if gate.python_module is not None and find_spec(gate.python_module) is None:
        return done(NOT_RUN, f"python module {gate.python_module!r} is not installed")
    if gate.executable is not None and which(gate.executable) is None:
        return done(NOT_RUN, f"executable {gate.executable!r} not found on PATH")
    try:
        proc = runner(list(gate.argv), cwd=root, env=dict(env), capture_output=True,
                      text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return done(FAIL, f"timed out after {timeout_s:g}s")
    except OSError as exc:
        return done(NOT_RUN, f"could not start: {type(exc).__name__}")
    output = clip_output((proc.stdout or "") + (proc.stderr or ""))
    return done(PASS if proc.returncode == 0 else FAIL, output, proc.returncode)


def select_gates(only: Sequence[str] | None, skip: Sequence[str] | None) -> list[Gate]:
    """Resolve --only/--skip against GATES, preserving the fixed order. ValueError if unknown."""
    names = list(only or []) + list(skip or [])
    unknown = sorted(set(names) - set(GATE_NAMES))
    if unknown:
        raise ValueError(f"unknown gate(s): {', '.join(unknown)}; known: {', '.join(GATE_NAMES)}")
    chosen = set(only) if only else set(GATE_NAMES)
    chosen -= set(skip or [])
    return [g for g in GATES if g.name in chosen]


def exit_code(results: Sequence[GateResult]) -> int:
    if any(r.status == FAIL for r in results):
        return EXIT_FAIL
    if any(r.status == NOT_RUN for r in results):
        return EXIT_NOT_RUN
    return EXIT_OK


def build_report(results: Sequence[GateResult], selected: Sequence[Gate]) -> dict[str, Any]:
    by_name = {r.name: r for r in results}
    complete = len(selected) == len(GATES) and all(by_name[g.name].status == PASS for g in selected)
    rows = []
    for gate in GATES:
        result = by_name.get(gate.name)
        if result is None:
            rows.append({"name": gate.name, "status": SKIPPED})
        else:
            rows.append({"name": gate.name, "status": result.status, "exit_code": result.exit_code,
                         "duration_s": result.duration_s, "detail": result.detail})
    return {
        "report_version": REPORT_VERSION,
        "complete": complete,
        "exit_code": exit_code(results),
        "gates": rows,
        "note": "author-side gate results; not independent review and not acceptance",
    }


def run_all(
    selected: Sequence[Gate],
    root: Path,
    env: Mapping[str, str],
    *,
    fail_fast: bool = False,
    **kwargs: Any,
) -> list[GateResult]:
    results: list[GateResult] = []
    for gate in selected:
        result = run_gate(gate, root, env, **kwargs)
        results.append(result)
        if fail_fast and result.status == FAIL:
            break
    return results


def format_human(report: Mapping[str, Any]) -> str:
    lines = [f"{row['status']:<8} {row['name']}" for row in report["gates"]]
    lines.append(f"complete={str(report['complete']).lower()} exit_code={report['exit_code']}")
    return "\n".join(lines)


def parse_names(text: str) -> list[str]:
    return [part for part in (p.strip() for p in text.split(",")) if part]


def main(argv: Sequence[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(prog="quality_gate", description=__doc__.splitlines()[0])
    parser.add_argument("--only", type=parse_names, help="comma-separated gates to run")
    parser.add_argument("--skip", type=parse_names, help="comma-separated gates to skip")
    parser.add_argument("--json", action="store_true", help="print the JSON report instead of text")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first FAIL")
    parser.add_argument("--network", action="store_true",
                        help=f"pass {NETWORK_ENV}=1 through if it is set (default: removed)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="per-gate seconds")
    parser.add_argument("--list", action="store_true", help="list gate names and exit")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if args.list:
        sys.stdout.write("\n".join(GATE_NAMES) + "\n")
        return EXIT_OK
    if args.timeout <= 0:
        sys.stderr.write("--timeout must be positive\n")
        return EXIT_USAGE
    try:
        selected = select_gates(args.only, args.skip)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_USAGE
    env = child_environment(os.environ, network=args.network)
    results = run_all(selected, root, env, fail_fast=args.fail_fast, timeout_s=args.timeout)
    report = build_report(results, selected)
    text = json.dumps(report, indent=2, sort_keys=True) if args.json else format_human(report)
    sys.stdout.write(text + "\n")
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
