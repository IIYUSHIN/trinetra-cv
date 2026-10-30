"""Security defaults and secret-redaction helpers (IMP-0013).

Standard library only. The Bible fixes only the prohibitions ("no secrets in
repo; no remote telemetry; no runtime downloads"); everything below is a
PROPOSED engineering decision and is documented as such.

Honest limits - read before relying on this module:

* Redaction is defence in depth, NOT a guarantee. The primary control is to
  never put a secret into a log line, error, or response in the first place;
  wrap secrets in `Secret` so they cannot be formatted by accident.
* Pattern redaction recognises well-known credential *shapes* only. An
  arbitrary high-entropy string without a recognisable shape or key name is
  not detected. Known secret values registered with `Redactor.with_secret` are
  removed wherever they appear verbatim (raw or percent-encoded).
* Over-redaction is the deliberate failure direction: any key whose name
  contains password/secret/token/... is masked even if its value is harmless.

All regular expressions use bounded repetition (no nested unbounded
quantifiers) so redaction time is linear in the input; inputs above
`MAX_REDACT_CHARS` are replaced wholesale rather than scanned.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, Final

from trinetra.errors import TrinetraError

REDACTED: Final = "[REDACTED]"
OVERSIZED_MARKER: Final = "[REDACTED: oversized text]"
MIN_SECRET_LENGTH: Final = 8
MAX_SECRET_LENGTH: Final = 4096
MAX_REDACT_CHARS: Final = 1_000_000
MAX_DEPTH: Final = 12
MAX_ITEMS: Final = 10_000

DEFAULT_BIND_HOST: Final = "127.0.0.1"


class SecurityError(TrinetraError):
    code = "SECURITY_INVALID"
    default_message = "invalid security input"


# ------------------------------------------------------------------- Secret
class Secret:
    """A string that cannot be printed, formatted, copied, pickled or hashed by accident."""

    __slots__ = ("_value",)
    _value: str
    __hash__ = None  # type: ignore[assignment]  # unhashable on purpose

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise SecurityError("a Secret must wrap a str", details={"name": "value"})
        if not 1 <= len(value) <= MAX_SECRET_LENGTH:
            raise SecurityError(f"a Secret must be 1..{MAX_SECRET_LENGTH} characters",
                                details={"name": "value"})
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        """The only way to read the value; every call site is greppable."""
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return "Secret(***)"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode("utf-8"), other._value.encode("utf-8"))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Secret is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Secret is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("Secret must not be pickled")

    def __copy__(self) -> Secret:
        raise TypeError("Secret must not be copied")

    def __deepcopy__(self, memo: object) -> Secret:
        raise TypeError("Secret must not be copied")


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    """Timing-safe equality for two strings or two byte strings (never mixed)."""
    if isinstance(a, str) and isinstance(b, str):
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    if isinstance(a, bytes) and isinstance(b, bytes):
        return hmac.compare_digest(a, b)
    raise SecurityError("both operands must be str or both bytes", details={"name": "operands"})


def percent_encode(value: str) -> str:
    """RFC 3986 percent-encoding of every byte outside the unreserved set.

    Equivalent to ``urllib.parse.quote(value, safe="")``; reimplemented because
    the import-boundary rules (IMP-0004) forbid ``urllib`` in package code.
    """
    unreserved = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
    return "".join(
        chr(b) if b in unreserved else f"%{b:02X}" for b in value.encode("utf-8", "surrogatepass")
    )


# ----------------------------------------------------------------- patterns
_VALUE: Final = r"(?:\"[^\"\r\n]{0,512}\"|'[^'\r\n]{0,512}'|[^\s\"',;&]{1,512})"
_SENSITIVE_WORDS: Final = (
    r"(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey|private[_-]?key|"
    r"credential|access[_-]?key|auth[_-]?key|client[_-]?secret)"
)
PATTERNS: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    ("pem-private-key",
     re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----[\s\S]{0,16384}?(?:-----END [A-Z ]{0,40}"
                r"PRIVATE KEY-----|\Z)"), REDACTED),
    ("authorization-header",
     re.compile(r"(\b(?:proxy-)?authorization\b[ \t]*[:=][ \t]*)(?:(?:bearer|basic|token|digest)[ \t]+)?"
                r"[^\s\"',;]{1,2048}", re.IGNORECASE), r"\1" + REDACTED),
    ("sensitive-assignment",
     re.compile(r"(\b[a-z0-9_.-]{0,32}" + _SENSITIVE_WORDS + r"[a-z0-9_.-]{0,16}\b[ \t]*[:=][ \t]*)"
                + _VALUE, re.IGNORECASE), r"\1" + REDACTED),
    ("url-userinfo",
     re.compile(r"(\b[a-z][a-z0-9+.-]{1,15}://[^\s/:@]{1,64}:)[^\s/@]{1,128}(@)", re.IGNORECASE),
     r"\1" + REDACTED + r"\2"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,2048}"),
     REDACTED),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})"),
     REDACTED),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,255}"), REDACTED),
    ("api-key-sk", re.compile(r"\bsk-[A-Za-z0-9_-]{20,255}"), REDACTED),
)
_SENSITIVE_KEY_RE: Final = re.compile(
    r"password|passwd|pwd|passphrase|secret|token|apikey|authorization|privatekey|credential|"
    r"cookie|accesskey|authkey|sessionid|bearer"
)


def is_sensitive_key(name: object) -> bool:
    """True when a mapping key / field name suggests its value is a credential."""
    if not isinstance(name, str):
        return False
    return _SENSITIVE_KEY_RE.search(re.sub(r"[^a-z0-9]", "", name.lower())) is not None


# Rules precise enough to scan source code with (the assignment/authorization rules also
# match harmless code such as ``secret=True``).
HIGH_CONFIDENCE_RULES: Final = frozenset({
    "pem-private-key", "aws-access-key-id", "github-token", "slack-token", "api-key-sk", "jwt",
})


def find_secret_patterns(text: str, rules: frozenset[str] | None = None) -> list[tuple[str, int]]:
    """(rule name, offset) of every credential-shaped match; the match itself is not returned."""
    if len(text) > MAX_REDACT_CHARS:
        return [("oversized-text", 0)]
    return [
        (name, m.start())
        for name, pattern, _ in PATTERNS
        if rules is None or name in rules
        for m in pattern.finditer(text)
    ]


_FORBIDDEN_NAME_RE: Final = re.compile(
    r"(?:\.env(?:\..+)?|.+\.(?:pem|key|p12|pfx)|id_(?:rsa|dsa|ecdsa|ed25519))", re.IGNORECASE)
_FORBIDDEN_DIRS: Final = frozenset({"secrets", "private_keys"})


def is_forbidden_repo_path(path: str) -> bool:
    """True for paths that must never be committed (mirrors the IMP-0001 .gitignore rules)."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(p.lower() in _FORBIDDEN_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    return name.lower() != ".env.example" and _FORBIDDEN_NAME_RE.fullmatch(name) is not None


# ----------------------------------------------------------------- Redactor
class Redactor:
    """Immutable redaction policy: credential shapes + explicitly registered secret values."""

    __slots__ = ("_known", "_raw")
    _known: tuple[str, ...]
    _raw: tuple[str, ...]

    def __init__(self, known: Iterable[str] = ()) -> None:
        values: set[str] = set()
        raw: list[str] = []
        for value in known:
            if not isinstance(value, str) or len(value) < MIN_SECRET_LENGTH:
                raise SecurityError(
                    f"registered secrets must be str of at least {MIN_SECRET_LENGTH} characters "
                    "(shorter values cannot be redacted reliably)", details={"name": "known"})
            raw.append(value)
            values.add(value)
            values.add(percent_encode(value))
        # longest first so a value that contains another is replaced whole
        object.__setattr__(self, "_known", tuple(sorted(values, key=lambda v: (-len(v), v))))
        object.__setattr__(self, "_raw", tuple(raw))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Redactor is immutable")

    def __repr__(self) -> str:
        return f"Redactor(known_values={len(self._known)})"

    def with_secret(self, secret: Secret | str) -> Redactor:
        value = secret.reveal() if isinstance(secret, Secret) else secret
        return Redactor([*self._raw, value])

    def redact_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise SecurityError("text must be a str", details={"name": "text"})
        if len(text) > MAX_REDACT_CHARS:
            return OVERSIZED_MARKER
        for value in self._known:
            if value in text:
                text = text.replace(value, REDACTED)
        for _, pattern, replacement in PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def redact_value(self, value: Any, _depth: int = 0) -> Any:
        """Deep copy of `value` with strings redacted and sensitive-key values masked."""
        if isinstance(value, str):
            return self.redact_text(value)
        if _depth >= MAX_DEPTH:
            return REDACTED
        if isinstance(value, Secret):
            return REDACTED
        if isinstance(value, Mapping):
            out: dict[Any, Any] = {}
            for i, (key, item) in enumerate(value.items()):
                if i >= MAX_ITEMS:
                    out["_truncated"] = True
                    break
                safe_key = self.redact_text(key) if isinstance(key, str) else key
                out[safe_key] = REDACTED if is_sensitive_key(key) else self.redact_value(item, _depth + 1)
            return out
        if isinstance(value, list | tuple):
            return [self.redact_value(item, _depth + 1) for item in list(value)[:MAX_ITEMS]]
        return value


DEFAULT_REDACTOR: Final = Redactor()


# --------------------------------------------------------------- bind / ports
def validate_port(port: object) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SecurityError("port must be an int in 1..65535", details={"name": "port"})
    return port


def validate_bind_host(host: object, *, allow_non_loopback: bool = False) -> str:
    """Default-deny network exposure: only loopback unless explicitly allowed.

    Hostnames other than ``localhost`` are refused outright (resolution could
    point anywhere and the product is offline); with `allow_non_loopback`
    only IP literals are accepted. Wildcards (0.0.0.0, ::) are never loopback.
    """
    if not isinstance(host, str) or not host or host != host.strip() or "%" in host:
        raise SecurityError("bind host must be a plain IP literal or 'localhost'", details={"name": "host"})
    if host == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise SecurityError(
            "bind host must be an IP literal or 'localhost'", details={"name": "host"}
        ) from exc
    mapped = getattr(address, "ipv4_mapped", None)
    is_loopback = address.is_loopback or (mapped is not None and mapped.is_loopback)
    if not is_loopback and not allow_non_loopback:
        raise SecurityError("non-loopback bind requires allow_non_loopback=True", details={"name": "host"})
    return host


# ------------------------------------------------------------- response headers
API_RESPONSE_HEADERS: Final[Mapping[str, str]] = MappingProxyType({
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
})


def security_header_problems(headers: Mapping[str, str]) -> list[str]:
    """Names of required headers that are absent or weaker than API_RESPONSE_HEADERS."""
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    problems = []
    for name, expected in API_RESPONSE_HEADERS.items():
        actual = lowered.get(name.lower())
        if actual is None:
            problems.append(f"{name}: missing")
        elif name == "Cache-Control":
            if "no-store" not in actual.lower().replace(" ", "").split(","):
                problems.append(f"{name}: must contain no-store")
        elif name == "Content-Security-Policy":
            directives = {d.strip().lower() for d in actual.split(";")}
            if "default-src 'none'" not in directives or "frame-ancestors 'none'" not in directives:
                problems.append(f"{name}: must contain default-src 'none' and frame-ancestors 'none'")
        elif actual.strip().lower() != expected.lower():
            problems.append(f"{name}: expected {expected!r}")
    return problems
