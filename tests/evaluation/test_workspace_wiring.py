"""Lane workspace wiring: the evaluation package is importable by tests and invisible to production."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_evaluation_package_is_importable_by_tests():
    import trinetra_eval

    assert trinetra_eval.__doc__


def test_production_tree_never_mentions_the_evaluation_package():
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "backend" / "src" / "trinetra").rglob("*.py")
        if "trinetra_eval" in p.read_text(encoding="utf-8")
        and p.name != "boundaries.py"
    ]
    assert offenders == []


def test_importing_production_does_not_import_evaluation():
    code = "import sys, trinetra; sys.exit(1 if 'trinetra_eval' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
