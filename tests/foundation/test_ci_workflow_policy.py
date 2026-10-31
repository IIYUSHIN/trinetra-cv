"""Policy tests for .github/workflows/quality-gate.yml (stdlib only; text-level checks)."""

from __future__ import annotations

import re

import pytest

from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text("utf-8")


def test_only_the_expected_workflow_files_exist():
    names = sorted(p.name for p in (REPO_ROOT / ".github" / "workflows").iterdir())
    assert names == ["quality-gate.yml"]


def test_least_privilege_token_and_no_dangerous_triggers(text):
    assert re.search(r"(?m)^permissions:\n  contents: read\n", text)
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert "write" not in re.sub(r"#.*", "", text).replace("persist-credentials", "")


def test_every_action_is_pinned_to_a_full_commit_sha(text):
    uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", text)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_no_secrets_or_telemetry_and_bounded_runtime(text):
    body = re.sub(r"#.*", "", text)
    assert "secrets." not in body and "${{ github.token }}" not in body
    assert re.search(r"(?m)^    timeout-minutes: \d+$", text)
    assert "concurrency:" in text and "persist-credentials: false" in text


def test_it_runs_the_real_quality_runner_from_a_frozen_lock(text):
    assert "uv sync --frozen --extra persistence" in text
    assert "scripts/quality_gate.py" in text
    assert 'python-version: "3.12"' in text
