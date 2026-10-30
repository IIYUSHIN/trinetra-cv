"""IMP-0014 tests: local / CI quality-gate runner (scripts/quality_gate.py)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "quality_gate.py"


def load_script():
    spec = importlib.util.spec_from_file_location("quality_gate_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


qg = load_script()
ENV = {"PATH": "/usr/bin"}


def proc(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def fixed(code=0, out="", err=""):
    """A runner double that always returns the same completed process."""

    def runner(argv, **kwargs):
        return proc(code, out, err)

    return runner


def forbidden(argv, **kwargs):
    pytest.fail("the tool must not be executed")


def external(name="g", module=None, exe=None):
    return qg.Gate(name, ("tool",), python_module=module, executable=exe)


def run(gate, **kw):
    kw.setdefault("which", lambda _name: "/bin/x")
    kw.setdefault("find_spec", lambda _name: object())
    return qg.run_gate(gate, REPO_ROOT, ENV, **kw)


# ------------------------------------------------------------------ happy path
def test_zero_exit_is_pass_and_records_exit_code_and_output():
    result = run(external(), runner=fixed(0, "all good\n"))
    assert (result.status, result.exit_code, result.detail) == (qg.PASS, 0, "all good")


def test_nonzero_exit_is_fail_with_output_tail():
    result = run(external(), runner=fixed(3, "", "boom"))
    assert (result.status, result.exit_code, result.detail) == (qg.FAIL, 3, "boom")


def test_runner_receives_argv_cwd_env_and_timeout():
    seen = {}

    def runner(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return proc()

    run(external(), runner=runner, timeout_s=7)
    assert seen["argv"] == ["tool"] and seen["cwd"] == REPO_ROOT and seen["env"] == ENV
    assert seen["timeout"] == 7 and seen["check"] is False and seen["capture_output"] is True


# ------------------------------------------------- honesty: never fake a PASS
def test_missing_python_module_is_not_run_not_pass():
    result = run(external(module="ruff"), find_spec=lambda _n: None,
                 runner=forbidden)
    assert result.status == qg.NOT_RUN and "ruff" in result.detail


def test_missing_executable_is_not_run_not_pass():
    result = run(external(exe="uv"), which=lambda _n: None,
                 runner=forbidden)
    assert result.status == qg.NOT_RUN and "uv" in result.detail


def test_failure_to_start_is_not_run():
    def runner(argv, **kwargs):
        raise PermissionError

    assert run(external(), runner=runner).status == qg.NOT_RUN


def test_timeout_is_fail():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired("tool", 1)

    result = run(external(), runner=runner, timeout_s=1)
    assert result.status == qg.FAIL and "timed out" in result.detail


def test_crashing_in_process_gate_is_fail_not_pass():
    def boom(_root):
        raise RuntimeError("x")

    result = run(qg.Gate("p", in_process=boom))
    assert result.status == qg.FAIL and "RuntimeError" in result.detail


def test_in_process_gate_status_is_passed_through():
    for status in (qg.PASS, qg.FAIL, qg.NOT_RUN):
        assert run(qg.Gate("p", in_process=lambda _r, s=status: (s, "d"))).status == status


# ------------------------------------------------------------ output handling
def test_output_is_redacted_and_bounded():
    secret = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"
    noisy = "\n".join(f"line {i}" for i in range(500)) + f"\nleak {secret}\n"
    result = run(external(), runner=fixed(1, noisy))
    assert secret not in result.detail
    assert len(result.detail.splitlines()) <= qg.OUTPUT_TAIL_LINES
    assert len(result.detail) <= qg.OUTPUT_TAIL_CHARS


def test_clip_output_keeps_the_tail_not_the_head():
    text = "\n".join(str(i) for i in range(1000))
    assert qg.clip_output(text).splitlines()[-1] == "999"
    assert qg.clip_output("x" * 100_000) == "x" * qg.OUTPUT_TAIL_CHARS


# ------------------------------------------------------------------ environment
def test_child_environment_drops_trinetra_vars_and_forces_offline():
    base = {"PATH": "/bin", "TRINETRA_STATE_DIR": "/x", qg.NETWORK_ENV: "1"}
    env = qg.child_environment(base, network=False)
    assert env["PATH"] == "/bin" and env["UV_OFFLINE"] == "1"
    assert not any(k.startswith("TRINETRA_") for k in env)
    assert qg.child_environment(base, network=True)[qg.NETWORK_ENV] == "1"
    assert qg.NETWORK_ENV not in qg.child_environment({"PATH": "/bin"}, network=True)
    assert qg.child_environment({"UV_OFFLINE": "0"}, network=False)["UV_OFFLINE"] == "1"


# -------------------------------------------------------------- selection logic
def test_gate_order_and_names_are_fixed_and_unique():
    assert qg.GATE_NAMES == ("ruff", "mypy", "boundaries", "repo-secrets", "uv-lock",
                             "diff-check", "pytest")
    assert len(set(qg.GATE_NAMES)) == len(qg.GATE_NAMES)


def test_select_preserves_fixed_order_regardless_of_request_order():
    assert [g.name for g in qg.select_gates(["pytest", "ruff"], None)] == ["ruff", "pytest"]
    assert [g.name for g in qg.select_gates(None, ["pytest"])] == list(qg.GATE_NAMES[:-1])
    assert [g.name for g in qg.select_gates(None, None)] == list(qg.GATE_NAMES)
    assert qg.select_gates(["ruff"], ["ruff"]) == []


@pytest.mark.parametrize(("only", "skip"), [(["nope"], None), (None, ["nope"]), (["ruff", "x"], ["y"])])
def test_unknown_gate_names_are_rejected(only, skip):
    with pytest.raises(ValueError, match="unknown gate"):
        qg.select_gates(only, skip)


# ---------------------------------------------------------- exit code & report
def result(name, status):
    return qg.GateResult(name, status)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [([], 0), ([qg.PASS], 0), ([qg.PASS, qg.NOT_RUN], 2), ([qg.NOT_RUN, qg.FAIL], 1),
     ([qg.FAIL], 1), ([qg.PASS, qg.PASS, qg.FAIL, qg.NOT_RUN], 1)],
)
def test_exit_code_mapping(statuses, expected):
    assert qg.exit_code([result(f"g{i}", s) for i, s in enumerate(statuses)]) == expected


def test_report_is_complete_only_when_every_gate_ran_and_passed():
    all_pass = [result(g.name, qg.PASS) for g in qg.GATES]
    assert qg.build_report(all_pass, list(qg.GATES))["complete"] is True
    partial = qg.build_report(all_pass[:-1], list(qg.GATES[:-1]))
    assert partial["complete"] is False and partial["gates"][-1]["status"] == qg.SKIPPED
    not_run = [*all_pass[:-1], result("pytest", qg.NOT_RUN)]
    report = qg.build_report(not_run, list(qg.GATES))
    assert report["complete"] is False and report["exit_code"] == 2


def test_report_is_json_serialisable_ordered_and_disclaims_acceptance():
    report = qg.build_report([result("ruff", qg.PASS)], [qg.GATES[0]])
    json.dumps(report)
    assert [g["name"] for g in report["gates"]] == list(qg.GATE_NAMES)
    assert report["report_version"] == 1 and "not independent review" in report["note"]
    assert "ACCEPTED" not in json.dumps(report)


def test_fail_fast_stops_after_first_failure():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return proc(1)

    gates = [external("a"), external("b")]
    out = qg.run_all(gates, REPO_ROOT, ENV, fail_fast=True, runner=runner,
                     which=lambda _n: "x", find_spec=lambda _n: object())
    assert [r.name for r in out] == ["a"] and len(calls) == 1
    out = qg.run_all(gates, REPO_ROOT, ENV, runner=runner,
                     which=lambda _n: "x", find_spec=lambda _n: object())
    assert [r.name for r in out] == ["a", "b"]


# ------------------------------------------------------------ real, in-process
def test_boundaries_gate_passes_on_the_real_tree_and_fails_on_a_planted_violation(tmp_path):
    assert qg.gate_boundaries(REPO_ROOT)[0] == qg.PASS
    pkg = tmp_path / "backend" / "src" / "trinetra"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "errors.py").write_text("import requests\n")
    status, detail = qg.gate_boundaries(tmp_path)
    assert status == qg.FAIL and "DEP-001" in detail


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


def test_repo_secrets_gate_passes_on_the_real_repository():
    assert qg.gate_repo_secrets(REPO_ROOT)[0] == qg.PASS


def test_repo_secrets_gate_flags_secrets_and_forbidden_paths_without_echoing_them(tmp_path):
    root = make_repo(tmp_path)
    token = "gh" + "p_" + "a1B2c3D4e5" * 4
    (root / "notes.txt").write_text(f"token {token}\n")
    (root / "prod.env").write_text("x=1\n")
    (root / ".env").write_text("A=1\n")
    (root / "binary.bin").write_bytes(b"\xff\xfe\x00")
    status, detail = qg.gate_repo_secrets(root)
    assert status == qg.FAIL
    assert "notes.txt" in detail and ".env: forbidden path" in detail and token not in detail
    assert "binary.bin" not in detail


def test_repo_secrets_gate_clean_repo_and_non_repo(tmp_path):
    (tmp_path / "r").mkdir()
    root = make_repo(tmp_path / "r")
    (root / "ok.txt").write_text("hello\n")
    assert qg.gate_repo_secrets(root)[0] == qg.PASS
    plain = tmp_path / "plain"
    plain.mkdir()
    assert qg.gate_repo_secrets(plain)[0] == qg.NOT_RUN


def test_in_process_gates_are_not_run_when_the_package_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "trinetra.boundaries", None)
    monkeypatch.setitem(sys.modules, "trinetra.security", None)
    assert qg.gate_boundaries(REPO_ROOT)[0] == qg.NOT_RUN
    assert qg.gate_repo_secrets(REPO_ROOT)[0] == qg.NOT_RUN


# ------------------------------------------------------------------ CLI (real)
def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          check=False, cwd=REPO_ROOT, timeout=300)


def test_cli_list_and_usage_errors():
    listed = cli("--list")
    assert listed.returncode == 0 and listed.stdout.split() == list(qg.GATE_NAMES)
    assert cli("--only", "nope").returncode == 64
    assert cli("--timeout", "0").returncode == 64
    assert cli("--bogus-flag").returncode == 64


def test_cli_real_run_of_the_in_process_gates_emits_valid_json_and_marks_incomplete():
    done = cli("--only", "boundaries,repo-secrets", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads(done.stdout)
    statuses = {g["name"]: g["status"] for g in report["gates"]}
    assert statuses["boundaries"] == statuses["repo-secrets"] == qg.PASS
    assert statuses["pytest"] == qg.SKIPPED and report["complete"] is False


def test_cli_text_output_lists_each_gate():
    done = cli("--only", "boundaries")
    assert done.returncode == 0 and "PASS     boundaries" in done.stdout
    assert "complete=false" in done.stdout


def test_cli_exit_code_reflects_a_real_failing_gate(tmp_path):
    """Run main() against a copy of the tree with a planted boundary violation."""
    pkg = tmp_path / "backend" / "src" / "trinetra"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "errors.py").write_text("import requests\n")
    code = qg.main(["--only", "boundaries"], root=tmp_path)
    assert code == 1


def test_script_is_stdlib_only_at_import_time():
    text = SCRIPT.read_text()
    top = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in top if "trinetra" in ln]
    assert "urllib" not in text and "socket" not in text and "requests" not in text
    assert Path(SCRIPT).stat().st_size > 0
