"""Actual source, subprocess import closure and offline-built production wheel firewall."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from trinetra_eval.canary import LeakageCanary
from trinetra_eval.production_firewall import production_firewall_violations
from trinetra_eval.release_scan import assert_no_leakage

ROOT = Path(__file__).resolve().parents[2]


def test_real_production_source_has_no_evaluation_dependencies():
    assert production_firewall_violations(ROOT / "backend/src/trinetra") == ()


@pytest.mark.parametrize(
    "source",
    ["import trinetra_eval", "from importlib import import_module as load; load('trinetra_eval.hidden')"],
)
def test_firewall_rejects_direct_and_aliased_imports(tmp_path, source):
    package = tmp_path / "product"
    package.mkdir()
    (package / "module.py").write_text(source)
    assert production_firewall_violations(package) == ("module.py",)


def test_missing_tree_is_not_a_success(tmp_path):
    assert production_firewall_violations(tmp_path / "missing")


def test_production_import_closure_with_evaluation_denied():
    code = """
import importlib,importlib.abc,pkgutil,sys,trinetra
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='trinetra_eval' or fullname.startswith('trinetra_eval.'):
            raise RuntimeError('evaluation imported by production')
sys.meta_path.insert(0,Deny())
for module in pkgutil.walk_packages(trinetra.__path__,'trinetra.'):
    importlib.import_module(module.name)
assert not any(n=='trinetra_eval' or n.startswith('trinetra_eval.') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr.decode()


def test_offline_built_production_wheel_excludes_evaluation(tmp_path):
    output = tmp_path / "wheels"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(output)],
        cwd=ROOT,
        env={**os.environ, "UV_OFFLINE": "1"},
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode()
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        assert not any("trinetra_eval" in name for name in archive.namelist())
        assert any(name == "trinetra/__init__.py" for name in archive.namelist())
    assert_no_leakage((wheels[0],), (LeakageCanary(),))
