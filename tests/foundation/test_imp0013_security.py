"""IMP-0013 tests: security defaults and secret-redaction helpers.

Every fake credential is assembled at runtime from fragments, so this file
contains no credential-shaped literal and the repo-wide "no secrets in repo"
scan below can stay strict.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import pickle
import subprocess
import time
from urllib.parse import quote

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra import security
from trinetra.errors import TrinetraError
from trinetra.security import (
    API_RESPONSE_HEADERS,
    DEFAULT_BIND_HOST,
    DEFAULT_REDACTOR,
    HIGH_CONFIDENCE_RULES,
    MAX_REDACT_CHARS,
    MAX_SECRET_LENGTH,
    MIN_SECRET_LENGTH,
    OVERSIZED_MARKER,
    REDACTED,
    Redactor,
    Secret,
    SecurityError,
    constant_time_equals,
    find_secret_patterns,
    is_forbidden_repo_path,
    is_sensitive_key,
    percent_encode,
    security_header_problems,
    validate_bind_host,
    validate_port,
)
from trinetra.structured_log import configure_logging, log_event

# --- runtime-assembled fake credentials (never literal) -------------------------
AWS_KEY = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"
GH_TOKEN = "gh" + "p_" + "a1B2c3D4e5" * 4
JWT = ".".join([
    "ey" + "JhbGciOiJIUzI1NiJ9",
    "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0",
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])
PEM_BEGIN = "-----" + "BEGIN RSA PRIVATE" + " KEY-----"
PEM_END = "-----" + "END RSA PRIVATE" + " KEY-----"
PEM = PEM_BEGIN + "\nMIIEowIBAAKCAQEA7Zq\nabcdefghijklmnop\n" + PEM_END
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
SK_KEY = "s" + "k-" + "ant" + "A1b2C3d4E5f6G7h8I9j0K1"
PW = "pass" + "word"
SECRET_VALUE = "correct-horse-battery"


# -------------------------------------------------------------------- Secret
def test_secret_never_reveals_itself_by_accident():
    secret = Secret(SECRET_VALUE)
    for rendered in (repr(secret), str(secret), f"{secret}", f"{secret!r}", "%s" % secret,  # noqa: UP031
                     f"{secret}", format(secret, ">30")):
        assert SECRET_VALUE not in rendered and "***" in rendered
    assert secret.reveal() == SECRET_VALUE


def test_secret_cannot_be_serialised_hashed_copied_or_mutated():
    secret = Secret(SECRET_VALUE)
    with pytest.raises(TypeError):
        json.dumps({"k": secret})
    with pytest.raises(TypeError):
        hash(secret)
    with pytest.raises(TypeError):
        {secret: 1}  # noqa: B018
    with pytest.raises(TypeError):
        {secret}  # noqa: B018
    for action in (lambda: pickle.dumps(secret), lambda: copy.copy(secret), lambda: copy.deepcopy(secret)):
        with pytest.raises(TypeError):
            action()
    with pytest.raises(AttributeError):
        secret._value = "x"
    with pytest.raises(AttributeError):
        del secret._value


def test_secret_equality_is_value_based_and_type_strict():
    assert Secret("a" * 10) == Secret("a" * 10)
    assert Secret("a" * 10) != Secret("a" * 9 + "b")
    assert Secret("a" * 10) != "a" * 10
    assert (Secret("x") == object()) is False


def test_secret_is_not_leaked_through_logging_or_exceptions():
    stream = io.StringIO()
    logger = logging.getLogger("trinetra.test.secretleak")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("value=%s repr=%r", Secret(SECRET_VALUE), Secret(SECRET_VALUE))
        with pytest.raises(ValueError) as info:
            raise ValueError(Secret(SECRET_VALUE))
        assert SECRET_VALUE not in str(info.value)
    finally:
        logger.removeHandler(handler)
    assert SECRET_VALUE not in stream.getvalue()


@pytest.mark.parametrize("bad", [b"bytes", 5, None, ["x"], Secret("abc")])
def test_secret_requires_str(bad):
    with pytest.raises(SecurityError, match="str"):
        Secret(bad)  # type: ignore[arg-type]


def test_secret_length_boundaries():
    assert Secret("x").reveal() == "x"
    assert len(Secret("x" * MAX_SECRET_LENGTH).reveal()) == MAX_SECRET_LENGTH
    for bad in ("", "x" * (MAX_SECRET_LENGTH + 1)):
        with pytest.raises(SecurityError, match="characters"):
            Secret(bad)


def test_constant_time_equals():
    assert constant_time_equals("abc", "abc") and not constant_time_equals("abc", "abd")
    assert constant_time_equals(b"abc", b"abc") and not constant_time_equals(b"abc", b"ab")
    assert constant_time_equals("", "") and constant_time_equals("é", "é")
    assert not constant_time_equals("é", "é")  # no normalisation
    for a, b in (("a", b"a"), (b"a", "a"), (1, 1), (None, None)):
        with pytest.raises(SecurityError):
            constant_time_equals(a, b)  # type: ignore[arg-type]


# ------------------------------------------------------------- percent_encode
@pytest.mark.parametrize(
    "text",
    ["", "abc", "a b", "a/b?c=d&e", "é", "\U0001f600", "~-._", "%41", "a+b", "\x00\x7f"],
)
def test_percent_encode_matches_urllib_quote(text):
    assert percent_encode(text) == quote(text, safe="")


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",))))
def test_property_percent_encode_matches_urllib_quote(text):
    assert percent_encode(text) == quote(text, safe="")


# ------------------------------------------------------------ pattern redaction
@pytest.mark.parametrize(
    ("label", "text", "secret_part"),
    [
        ("aws", f"id {AWS_KEY} used", AWS_KEY),
        ("github", f"token: {GH_TOKEN}", GH_TOKEN),
        ("jwt", f"cookie {JWT}", JWT),
        ("slack", f"hook {SLACK} sent", SLACK),
        ("sk", f"key {SK_KEY}", SK_KEY),
        ("pem", f"before\n{PEM}\nafter", "MIIEowIBAAKCAQEA7Zq"),
        ("pem-unterminated", f"before\n{PEM_BEGIN}\nMIIEowIBAAKCAQEA7Zq\nmore", "MIIEowIBAAKCAQEA7Zq"),
        ("bearer", "Authorization: Bearer abc123.def456-ghi789", "abc123.def456-ghi789"),
        ("basic", "authorization=Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("proxy-auth", "Proxy-Authorization: Bearer zzzzzzzz", "zzzzzzzz"),
        ("assign-eq", f"{PW}=hunter2xyz rest", "hunter2xyz"),
        ("assign-colon", f"{PW}: hunter2xyz", "hunter2xyz"),
        ("assign-quoted", f'{PW}="hunter two xyz"', "hunter two xyz"),
        ("assign-single", f"{PW}='hunter two xyz'", "hunter two xyz"),
        ("assign-prefixed", "db_" + PW + "=hunter2xyz", "hunter2xyz"),
        ("assign-api-key", "API_KEY=abcdef123456", "abcdef123456"),
        ("assign-client-secret", "client_secret = abcdef123456", "abcdef123456"),
        ("assign-query", "https://host/x?token=abcdef123456&a=1", "abcdef123456"),
        ("url-userinfo", "postgres://admin:s3cr3tpass@db.local:5432/x", "s3cr3tpass"),
    ],
)
def test_credential_shapes_are_redacted(label, text, secret_part):
    out = DEFAULT_REDACTOR.redact_text(text)
    assert secret_part not in out, (label, out)
    assert REDACTED in out


def test_labels_and_surrounding_text_survive_redaction():
    out = DEFAULT_REDACTOR.redact_text(f"user=bob {PW}=hunter2xyz port=5432")
    assert out == f"user=bob {PW}={REDACTED} port=5432"
    assert DEFAULT_REDACTOR.redact_text("Authorization: Bearer abcdefgh.ijkl").startswith("Authorization: ")
    assert DEFAULT_REDACTOR.redact_text("postgres://admin:s3cr3tpass@db/x") == f"postgres://admin:{REDACTED}@db/x"


@pytest.mark.parametrize(
    "text",
    ["", "hello world", "x = 5", "The " + PW + " policy requires 12 characters", "user=alice",
     "path=/etc/hosts",
     "sha256:" + "a" * 64, "2026-09-02T14:35:20Z", "AKIA", "ghp_short", "eyJ.eyJ.sig", "https://example.com/a/b",
     "postgres://db.local:5432/x", "BEGIN PRIVATE KEY",
     "-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----"],
)
def test_ordinary_text_is_left_alone(text):
    assert DEFAULT_REDACTOR.redact_text(text) == text


def test_redaction_is_idempotent_and_never_grows_unboundedly():
    text = f"{PW}=hunter2xyz {AWS_KEY} Authorization: Bearer abcdefgh\n{PEM}"
    once = DEFAULT_REDACTOR.redact_text(text)
    assert DEFAULT_REDACTOR.redact_text(once) == once
    assert len(once) < len(text)


def test_known_limits_are_real_and_documented():
    """Honest negative controls: an unlabeled high-entropy string is NOT detected."""
    opaque = "N3v3rS33nSh4p3Qw9ZxK2LmPo8"
    assert DEFAULT_REDACTOR.redact_text(f"value {opaque}") == f"value {opaque}"
    assert DEFAULT_REDACTOR.with_secret(opaque).redact_text(f"value {opaque}") == f"value {REDACTED}"
    # over-redaction is the deliberate failure direction
    assert DEFAULT_REDACTOR.redact_text("token_count=5") == f"token_count={REDACTED}"


# --------------------------------------------------------- registered values
def test_registered_values_are_removed_raw_and_percent_encoded():
    secret = "p@ss w0rd/long!"
    red = Redactor([secret])
    assert red.redact_text(f"a {secret} b") == f"a {REDACTED} b"
    assert red.redact_text(f"url?x={percent_encode(secret)}") == f"url?x={REDACTED}"
    assert (secret not in repr(red) and "1" in repr(red)) or "known_values" in repr(red)


def test_longest_registered_value_wins():
    red = Redactor(["abcdefgh", "abcdefghij"])
    assert red.redact_text("x abcdefghij y") == f"x {REDACTED} y"


@pytest.mark.parametrize("bad", ["", "short", "x" * (MIN_SECRET_LENGTH - 1), 5, None, b"abcdefgh"])
def test_unreliable_or_invalid_registered_values_rejected(bad):
    with pytest.raises(SecurityError, match="at least"):
        Redactor([bad])  # type: ignore[list-item]


def test_with_secret_is_functional_and_accepts_secret_objects():
    base = Redactor()
    extended = base.with_secret(Secret("registered-value-1")).with_secret("registered-value-2")
    assert base.redact_text("registered-value-1") == "registered-value-1"
    assert extended.redact_text("registered-value-1 registered-value-2") == f"{REDACTED} {REDACTED}"
    with pytest.raises(AttributeError):
        extended._known = ()  # type: ignore[misc]


def test_text_size_boundary_and_type():
    ok = "a" * MAX_REDACT_CHARS
    assert DEFAULT_REDACTOR.redact_text(ok) == ok
    assert DEFAULT_REDACTOR.redact_text(ok + "a") == OVERSIZED_MARKER
    with pytest.raises(SecurityError):
        DEFAULT_REDACTOR.redact_text(b"bytes")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "hostile",
    [
        "a" * 1_000_000,
        ("password" + " ") * 100_000,
        PEM_BEGIN + "x" * 900_000,
        "Authorization:" + " " * 900_000,
        ("a=" + "b" * 50 + " ") * 20_000,
        "://" + "u" * 900_000 + ":",
        "eyJ" + "a" * 900_000,
        "-----BEGIN " + "A" * 900_000,
        "key=" + '"' * 500_000,
    ],
)
def test_pathological_inputs_do_not_cause_catastrophic_backtracking(hostile):
    started = time.perf_counter()
    DEFAULT_REDACTOR.redact_text(hostile)
    assert time.perf_counter() - started < 10


# ----------------------------------------------------------------- key policy
@pytest.mark.parametrize(
    "key",
    ["password", "PASSWORD", "db_password", "apiKey", "api-key", "X-Api-Key", "Authorization", "Set-Cookie",
     "client_secret", "access_token", "SECRET_KEY", "private_key", "refresh-token", "tokenizer", "bearer",
     "session_id", "passphrase", "credentials"],
)
def test_sensitive_key_names(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["username", "email", "count", "path", "name", "passenger", "keyboard", "", 5, None]
)
def test_non_sensitive_key_names(key):
    assert not is_sensitive_key(key)


# ------------------------------------------------------------- deep redaction
def test_redact_value_masks_sensitive_keys_whatever_the_value_type():
    data = {"user": "bob", PW: "x", "api_key": None, "token": 12345, "auth_token": ["a", {"b": 1}],
            "nested": {"Authorization": "Bearer abc", "ok": [1, "fine", {"secret": {"deep": 1}}]}}
    out = DEFAULT_REDACTOR.redact_value(data)
    assert out == {"user": "bob", PW: REDACTED, "api_key": REDACTED, "token": REDACTED,
                   "auth_token": REDACTED,
                   "nested": {"Authorization": REDACTED, "ok": [1, "fine", {"secret": REDACTED}]}}


def test_redact_value_scrubs_strings_everywhere_and_never_mutates_input():
    data = {"msg": f"{PW}=hunter2xyz", "items": [AWS_KEY, ("t", AWS_KEY)], AWS_KEY: 1}
    snapshot = copy.deepcopy(data)
    out = DEFAULT_REDACTOR.redact_value(data)
    assert data == snapshot
    flat = json.dumps(out)
    assert AWS_KEY not in flat and "hunter2xyz" not in flat


def test_redact_value_handles_secret_objects_and_passes_plain_scalars():
    assert DEFAULT_REDACTOR.redact_value({"x": Secret(SECRET_VALUE)}) == {"x": REDACTED}
    assert [DEFAULT_REDACTOR.redact_value(v) for v in (1, 1.5, True, None)] == [1, 1.5, True, None]


def test_redact_value_depth_and_size_limits():
    deep: object = "leaf"
    for _ in range(security.MAX_DEPTH + 5):
        deep = [deep]
    assert REDACTED in json.dumps(DEFAULT_REDACTOR.redact_value(deep))
    big = {f"k{i}": i for i in range(security.MAX_ITEMS + 3)}
    out = DEFAULT_REDACTOR.redact_value(big)
    assert len(out) == security.MAX_ITEMS + 1 and out["_truncated"] is True


# ------------------------------------------------------------ bind / port rules
def test_default_bind_host_is_loopback():
    assert DEFAULT_BIND_HOST == "127.0.0.1" and validate_bind_host(DEFAULT_BIND_HOST) == DEFAULT_BIND_HOST


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "127.255.255.254", "::1", "localhost",
                                  "::ffff:127.0.0.1"])
def test_loopback_hosts_accepted(host):
    assert validate_bind_host(host) == host


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.5", "10.0.0.1", "8.8.8.8", "fe80::1", "::ffff:8.8.8.8", "128.0.0.1",
     "example.com", "localhost.", "LOCALHOST", "local", "", " 127.0.0.1", "127.0.0.1 ", "127.0.0.1\n",
     "fe80::1%eth0", "127.1", "0x7f.0.0.1", "2130706433", "127.0.0.1:80", "[::1]", None, 5, b"127.0.0.1"],
)
def test_non_loopback_or_malformed_hosts_rejected_by_default(host):
    with pytest.raises(SecurityError):
        validate_bind_host(host)


def test_explicit_opt_in_allows_ip_literals_only():
    for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.1"):
        assert validate_bind_host(host, allow_non_loopback=True) == host
    for host in ("example.com", "", " 10.0.0.1", "fe80::1%eth0", "10.0.0", None):
        with pytest.raises(SecurityError):
            validate_bind_host(host, allow_non_loopback=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [1, 80, 8080, 65535])
def test_valid_ports(port):
    assert validate_port(port) == port


@pytest.mark.parametrize("port", [0, -1, 65536, 100000, True, False, "80", 80.0, None, b"80"])
def test_invalid_ports(port):
    with pytest.raises(SecurityError):
        validate_port(port)


# ---------------------------------------------------------------- headers
def test_default_headers_satisfy_their_own_checker_and_are_immutable():
    assert security_header_problems(API_RESPONSE_HEADERS) == []
    with pytest.raises(TypeError):
        API_RESPONSE_HEADERS["X-Frame-Options"] = "ALLOWALL"  # type: ignore[index]
    assert set(API_RESPONSE_HEADERS) == {
        "Cache-Control", "Content-Security-Policy", "Cross-Origin-Resource-Policy", "Referrer-Policy",
        "X-Content-Type-Options", "X-Frame-Options"}


@pytest.mark.parametrize("name", sorted(API_RESPONSE_HEADERS))
def test_each_missing_header_is_reported(name):
    headers = {k: v for k, v in API_RESPONSE_HEADERS.items() if k != name}
    problems = security_header_problems(headers)
    assert len(problems) == 1 and problems[0].startswith(name)


@pytest.mark.parametrize(
    ("name", "weak"),
    [
        ("X-Content-Type-Options", "sniff"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Referrer-Policy", "unsafe-url"),
        ("Cross-Origin-Resource-Policy", "cross-origin"),
        ("Cache-Control", "public, max-age=3600"),
        ("Content-Security-Policy", "default-src *"),
        ("Content-Security-Policy", "default-src 'none'"),
        ("Content-Security-Policy", "frame-ancestors 'none'"),
    ],
)
def test_weakened_headers_are_reported(name, weak):
    headers = {**API_RESPONSE_HEADERS, name: weak}
    assert any(p.startswith(name) for p in security_header_problems(headers))


def test_header_checker_is_case_insensitive_and_tolerant_of_extras():
    headers = {k.lower(): v for k, v in API_RESPONSE_HEADERS.items()} | {"x-request-id": "1"}
    assert security_header_problems(headers) == []
    headers["cache-control"] = "private, No-Store"
    assert security_header_problems(headers) == []
    headers["cache-control"] = "no-store-ish"
    assert security_header_problems(headers)


# ------------------------------------------------------ logging integration
def make_logger(redactor=DEFAULT_REDACTOR, **kw):
    stream = io.StringIO()
    name = f"trinetra.test.sec.{id(stream)}"
    handler = configure_logging(level="DEBUG", stream=stream, logger_name=name, redactor=redactor, **kw)
    return logging.getLogger(name), stream, handler


def test_logging_redacts_messages_fields_and_sensitive_names_by_default():
    logger, stream, handler = make_logger()
    try:
        logger.info("login %s=hunter2xyz from %s", PW, AWS_KEY)
        log_event(logger, "INFO", "auth.attempt", api_key="anything-at-all",
                  note=f"Authorization: Bearer {JWT}",
                  nested={"refresh_token": "x" * 20, "ok": 1}, items=[GH_TOKEN])
    finally:
        handler.close()
    out = stream.getvalue()
    for leaked in ("hunter2xyz", AWS_KEY, "anything-at-all", JWT, "x" * 20, GH_TOKEN):
        assert leaked not in out, leaked
    first, second = (json.loads(line) for line in out.splitlines())
    assert second["api_key"] == REDACTED and second["nested"] == {"ok": 1, "refresh_token": REDACTED}
    assert first["message"].startswith("login ")


def test_secret_straddling_the_clip_boundary_is_still_redacted():
    logger, stream, handler = make_logger()
    try:
        log_event(logger, "INFO", "big", blob="x" * 2030 + " " + AWS_KEY + " " + "y" * 50)
    finally:
        handler.close()
    assert AWS_KEY not in stream.getvalue() and AWS_KEY[:10] not in stream.getvalue()


def test_secret_in_the_message_straddling_the_clip_boundary_is_redacted():
    logger, stream, handler = make_logger()
    try:
        logger.info("%s", "x" * 2030 + " " + AWS_KEY + " tail")
    finally:
        handler.close()
    assert AWS_KEY[:10] not in stream.getvalue()


def test_token_glued_to_word_characters_is_a_documented_unredacted_limit():
    """\\b-anchored token rules do not fire mid-identifier (precision choice, PROPOSED)."""
    glued = "x" * 5 + AWS_KEY
    assert DEFAULT_REDACTOR.redact_text(glued) == glued


def test_registered_value_redacted_in_logs_and_opt_out_is_explicit():
    custom = Redactor(["opaque-registered-token"])
    logger, stream, handler = make_logger(redactor=custom)
    try:
        log_event(logger, "INFO", "e", detail="saw opaque-registered-token here")
    finally:
        handler.close()
    assert "opaque-registered-token" not in stream.getvalue()
    logger, stream, handler = make_logger(redactor=None)  # explicit opt-out
    try:
        log_event(logger, "INFO", "e", detail=f"{PW}=hunter2xyz")
    finally:
        handler.close()
    assert "hunter2xyz" in stream.getvalue()


def test_redaction_never_breaks_the_one_line_json_contract():
    logger, stream, handler = make_logger()
    try:
        logger.info("%s\n%s=a\nb", PEM, PW)
    finally:
        handler.close()
    assert stream.getvalue().count("\n") == 1 and json.loads(stream.getvalue())["level"] == "INFO"


# ------------------------------------------------------ repo-wide policy scan
def tracked_and_untracked_files(root):
    cmd = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return [p for p in result.stdout.decode().split("\0") if p]


def test_no_secret_shaped_content_and_no_forbidden_paths_in_the_repository():
    from conftest import REPO_ROOT

    problems = []
    for rel in tracked_and_untracked_files(REPO_ROOT):
        if is_forbidden_repo_path(rel):
            problems.append(f"{rel}: forbidden path")
            continue
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = find_secret_patterns(text, HIGH_CONFIDENCE_RULES)
        problems += [f"{rel}@{offset}: {rule}" for rule, offset in hits]
    assert problems == []


@pytest.mark.parametrize("fake", [AWS_KEY, GH_TOKEN, JWT, SLACK, SK_KEY, PEM])
def test_the_scanner_itself_catches_what_it_is_meant_to(fake):
    assert find_secret_patterns(f"x = '{fake}'", HIGH_CONFIDENCE_RULES)


@pytest.mark.parametrize(
    ("path", "expected"),
    [(".env", True), (".env.local", True), ("sub/.env.production", True), (".env.example", False),
     ("a.pem", True), ("k.KEY", True), ("c.p12", True), ("c.pfx", True), ("id_rsa", True),
     ("id_ed25519", True),
     ("id_rsa.pub", False), ("x/secrets/a.txt", True), ("x/private_keys/a", True), ("src/secrets.py", False),
     ("README.md", False), ("", False), ("a\\secrets\\b.txt", True)],
)
def test_forbidden_path_matcher(path, expected):
    assert is_forbidden_repo_path(path) is expected


@pytest.mark.parametrize("path", [".env", ".env.local", "a.pem", "k.key", "c.p12", "c.pfx", "x/secrets/a.txt",
                                  "x/private_keys/a"])
def test_gitignore_actually_ignores_every_forbidden_path(path):
    from conftest import REPO_ROOT

    result = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", path],
                            capture_output=True, check=False)
    assert result.returncode == 0, f"{path} is not git-ignored"


def test_error_type():
    assert issubclass(SecurityError, TrinetraError) and SecurityError.code == "SECURITY_INVALID"
