"""IMP-0015: Foundation (I1) acceptance and import-boundary audit.

Author-run evidence about the Foundation module as a whole. It checks the I1
acceptance sentence - clean checkout can build the environment, import the
package, run quality/test commands and deterministically hash/canonicalize
fixtures with no network-dependent behaviour - and audits that every
import-boundary rule is both enforced and negatively tested.

It does NOT claim downstream validation, and a green run here is NOT acceptance:
independent review (REVIEW_PENDING) is still required.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from trinetra import boundaries, digest, jcs

VECTORS = json.loads((Path(__file__).parent / "golden" / "foundation_vectors.json").read_text("utf-8"))
SRC = REPO_ROOT / "backend" / "src"
TESTS = Path(__file__).parent
CLEAN_PATH = "/usr/bin:/bin"


def interpreter(code: str, *, env_extra: dict[str, str] | None = None, cwd: Path = REPO_ROOT,
                pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": CLEAN_PATH, "LC_ALL": "C.UTF-8"}
    env.update(env_extra or {})
    if pythonpath is not None:  # -I / -P ignore PYTHONPATH, so put the source tree first explicitly
        code = f"import sys; sys.path.insert(0, {str(pythonpath)!r})\n{code}"
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=False, cwd=cwd, env=env, timeout=300)


# ------------------------------------------------ known-answer vectors (oracle)
@pytest.mark.parametrize("vector", VECTORS["sha256"], ids=lambda v: v["id"])
def test_sha256_known_answers(vector):
    data = bytes.fromhex(vector["input"]["value"])
    assert digest.sha256_hex(data) == vector["sha256"]
    assert digest.format_digest(digest.sha256_bytes(data)) == "sha256:" + vector["sha256"]
    assert hashlib.sha256(data).hexdigest() == vector["sha256"]  # oracle self-check


@pytest.mark.parametrize("vector", VECTORS["jcs"], ids=lambda v: v["id"])
def test_jcs_known_answers(vector):
    canonical = jcs.canonicalize(vector["json"])
    assert canonical.decode("utf-8") == vector["canonical"]
    assert jcs.canonical_digest(vector["json"]) == "sha256:" + vector["canonical_sha256"]
    assert jcs.loads_strict(canonical.decode("utf-8")) is not None


def test_vector_file_is_well_formed_and_covers_each_family():
    assert {"sha256", "jcs"} <= set(VECTORS)
    assert len({v["id"] for v in VECTORS["jcs"]}) == len(VECTORS["jcs"])
    assert len(VECTORS["jcs"]) >= 6 and len(VECTORS["sha256"]) >= 2
    assert any(v["id"] == "rfc8785-appendix-b-becbf647612f3696" for v in VECTORS["jcs"])


# ------------------------------------------------------------- determinism
HASH_SCRIPT = (
    "import json,sys;from trinetra import jcs\n"
    f"vs=json.load(open({str(TESTS / 'golden' / 'foundation_vectors.json')!r}))['jcs']\n"
    "print(json.dumps([jcs.canonical_digest(v['json']) for v in vs]))\n"
)


def test_digests_do_not_depend_on_hash_randomisation_or_locale():
    outputs = set()
    for seed in ("0", "1", "12345", "random"):
        for locale in ("C.UTF-8", "C"):
            done = interpreter(HASH_SCRIPT, env_extra={"PYTHONHASHSEED": seed, "LC_ALL": locale},
                               pythonpath=SRC)
            assert done.returncode == 0, done.stderr
            outputs.add(done.stdout)
    assert len(outputs) == 1


# ------------------------------------------------- clean interpreter, no network
OFFLINE_IMPORT_SCRIPT = """
import importlib, pkgutil, socket, sys
calls = []
def trip(*a, **k):
    calls.append(a)
    raise RuntimeError("network touched")
socket.socket = trip
socket.create_connection = trip
socket.getaddrinfo = trip
before = set(sys.modules)
import trinetra
mods = sorted(m.name for m in pkgutil.walk_packages(trinetra.__path__, "trinetra."))
for name in mods:
    importlib.import_module(name)
third = sorted({m.split(".")[0] for m in set(sys.modules) - before
                if m.split(".")[0] not in sys.stdlib_module_names
                and not m.startswith("_") and m.split(".")[0] != "trinetra"})
print(repr({"modules": mods, "network_calls": len(calls), "third_party": third}))
"""


def test_every_production_module_imports_in_an_isolated_interpreter_without_network():
    done = interpreter(OFFLINE_IMPORT_SCRIPT, pythonpath=SRC)
    assert done.returncode == 0, done.stdout + done.stderr
    report = eval(done.stdout)  # noqa: S307 - repr of a dict of str/int/list produced by our own child
    assert report["network_calls"] == 0
    assert report["third_party"] == [], "Foundation (I1) must be standard-library only"
    expected = {f"trinetra.{m}" for m in boundaries.ROOT_MODULES}
    assert expected <= set(report["modules"])


def test_foundation_modules_are_stdlib_only_by_ast_not_just_by_runtime():
    third_party: set[str] = set()
    import ast

    for module in boundaries.ROOT_MODULES:
        tree = ast.parse((SRC / "trinetra" / f"{module}.py").read_text("utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level
                     else [])
            third_party |= {n.split(".")[0] for n in names
                            if n.split(".")[0] not in sys.stdlib_module_names | {"trinetra"}}
    assert third_party == set()


# ------------------------------------------------------------ clean checkout
def working_set(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                          "--exclude-standard"], capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


@pytest.fixture(scope="module")
def checkout(tmp_path_factory) -> Path:
    """The repository's working set (tracked + untracked, ignore rules honoured) copied
    to a fresh directory: stands in for 'clean checkout' without needing a commit."""
    dest = tmp_path_factory.mktemp("clean_checkout")
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True, capture_output=True)
    for rel in working_set(REPO_ROOT):
        source = REPO_ROOT / rel
        if source.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / rel)
    return dest


def test_checkout_copy_resolves_the_package_from_itself_and_runs_the_gate_scripts(checkout):
    where = interpreter("import trinetra;print(trinetra.__file__)", pythonpath=checkout / "backend" / "src",
                        cwd=checkout)
    assert where.returncode == 0, where.stderr
    assert where.stdout.strip().startswith(str(checkout))
    gate = subprocess.run(
        [sys.executable, str(checkout / "scripts" / "quality_gate.py"), "--only", "boundaries,repo-secrets",
         "--json"],
        capture_output=True, text=True, check=False, cwd=checkout, timeout=300,
        env={"PATH": CLEAN_PATH, "PYTHONPATH": str(checkout / "backend" / "src")},
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr
    ran = [g["status"] for g in json.loads(gate.stdout)["gates"] if g["status"] != "SKIPPED"]
    assert ran == ["PASS", "PASS"]


def test_checkout_copy_runs_a_foundation_test_subset_green(checkout):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TRINETRA_", "PYTEST"))}
    env["PYTHONPATH"] = str(checkout / "backend" / "src")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/foundation/test_imp0008_digest.py", "tests/foundation/test_imp0009_jcs.py",
         "tests/foundation/test_imp0004_import_boundary.py"],
        capture_output=True, text=True, check=False, cwd=checkout, env=env, timeout=600,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]
    assert " passed" in done.stdout and "failed" not in done.stdout


# ------------------------------------------------- import-boundary audit (meta)
def rule_ids_in_source() -> set[str]:
    return set(re.findall(r'"([A-Z]{3,5}-\d{3})"', (SRC / "trinetra" / "boundaries.py").read_text("utf-8")))


def test_the_real_source_tree_has_zero_boundary_violations():
    assert boundaries.check_tree(SRC) == []


def test_rule_inventory_is_the_documented_one():
    assert rule_ids_in_source() == {
        "BOOT-001", "BOOT-002", "BOOT-003", "BOOT-004", "BOOT-005", "BOOT-006",
        "PARSE-001", "PARSE-002", "DOM-001", "DOM-002", "APP-001", "API-001",
        "ROOT-001", "ROOT-002", "EVAL-001", "DEP-001", "DEP-002", "STD-001",
        "DYN-001", "WILD-001", "CYCLE-001",
    }


@pytest.mark.parametrize("rule", sorted(rule_ids_in_source()))
def test_every_boundary_rule_has_a_negative_test_that_asserts_it(rule):
    haystack = "\n".join(p.read_text("utf-8") for p in TESTS.glob("test_imp000[45]_*.py"))
    assert f'"{rule}"' in haystack, f"{rule} is enforced but no IMP-0004/0005 test asserts it"


def test_root_modules_each_have_a_mirrored_test_that_imports_them():
    for module in sorted(boundaries.ROOT_MODULES):
        pattern = re.compile(rf"from trinetra(?:\.{module} import|\s+import\s+[^\n]*\b{module}\b)|"
                             rf"import trinetra\.{module}\b")
        found = [p.name for p in TESTS.glob("test_imp*.py") if pattern.search(p.read_text("utf-8"))]
        assert found, f"no mirrored test imports trinetra.{module}"


def test_each_foundation_packet_has_its_mirrored_test_file():
    for number in range(2, 15):
        assert list(TESTS.glob(f"test_imp{number:04d}_*.py")), f"IMP-{number:04d} has no test file"


# -------------------------------------------------------- honesty guardrails
def test_tooling_output_and_vectors_never_claim_acceptance():
    gate = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "quality_gate.py"), "--only", "boundaries", "--json"],
        capture_output=True, text=True, check=False, cwd=REPO_ROOT, timeout=300,
    )
    assert gate.returncode == 0, gate.stderr
    report = json.loads(gate.stdout)
    assert report["complete"] is False and "not independent review" in report["note"]
    for text in (gate.stdout, (TESTS / "golden" / "foundation_vectors.json").read_text("utf-8")):
        assert not re.search(r"\bACCEPTED\b", text)
