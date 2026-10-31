"""Canaries in binary, encoded JSON, filenames, archives, logs and missing artifacts."""

import base64
import io
import json
import zipfile

import pytest

from trinetra_eval.canary import LeakageCanary
from trinetra_eval.release_scan import LeakageCanaryViolation, assert_no_leakage


@pytest.mark.parametrize("encoding", ["raw", "base64", "hex", "utf16", "json"])
def test_encoded_truth_is_detected_without_echoing_it(tmp_path, encoding):
    canary = LeakageCanary()
    token = canary.evaluator_private_token()
    payload = {
        "raw": token,
        "base64": base64.b64encode(token),
        "hex": token.hex().encode(),
        "utf16": token.decode().encode("utf-16-le"),
        "json": json.dumps({"nested": token.decode()}).replace("TF-", r"\u0054F-").encode(),
    }[encoding]
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(payload)
    with pytest.raises(LeakageCanaryViolation) as error:
        assert_no_leakage((artifact,), (canary,))
    assert token.decode() not in str(error.value)


def test_nested_wheel_and_filename_canaries_are_detected(tmp_path):
    canary = LeakageCanary()
    token = canary.evaluator_private_token()
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hidden.txt", token)
    outer = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested.zip", inner.getvalue())
    with pytest.raises(LeakageCanaryViolation):
        assert_no_leakage((outer,), (canary,))
    named = tmp_path / token.decode()
    named.write_bytes(b"clean")
    with pytest.raises(LeakageCanaryViolation):
        assert_no_leakage((named,), (canary,))


def test_clean_artifacts_pass_but_missing_symlink_and_bad_archive_do_not(tmp_path):
    canary = LeakageCanary()
    clean = tmp_path / "clean.json"
    clean.write_text('{"result":"UNRESOLVED"}')
    assert_no_leakage((clean,), (canary,))
    link = tmp_path / "link"
    link.symlink_to(clean)
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04broken")
    for path in (tmp_path / "missing", link, broken):
        with pytest.raises(LeakageCanaryViolation):
            assert_no_leakage((path,), (canary,))


def test_expanded_archive_budget_and_unreadable_tree_fail_closed(tmp_path, monkeypatch):
    from trinetra_eval import release_scan

    canary = LeakageCanary()
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("large.txt", b"x" * 2048)
    with monkeypatch.context() as patch:
        patch.setattr(release_scan, "MAX_BYTES", 1024)
        with pytest.raises(LeakageCanaryViolation):
            assert_no_leakage((archive,), (canary,))
    denied = tmp_path / "denied"
    denied.mkdir()
    (denied / "hidden.txt").write_bytes(canary.evaluator_private_token())
    denied.chmod(0)
    try:
        with pytest.raises(LeakageCanaryViolation):
            assert_no_leakage((tmp_path,), (canary,))
    finally:
        denied.chmod(0o700)
