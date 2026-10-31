# Codex A — L-A-1

Branch: codex-a/A1-tiir-wire
Exact lease base: 2a5db1a39402d8285ce48da71cd3436bce0db91a

Done (author implementation only): IMP-0284 errors/profile. Fixed profile, strict
integer constants, immutable configuration, 20 registered typed errors.
Focused tests: 15 passed. RED: missing module (exit 2); GREEN: exit 0.
Mutation: 2/2 killed (algorithm change and boolean coercion, each exit 1), restored.
No independent acceptance. All changes remain DOUBLE-CONTINGENT and
OUT-OF-ORDER-CONTINGENT on upstream I3/I6 gates.

In progress: IMP-0285 source-contract resolution; no local representation invented.
Next five: IMP-0285, IMP-0286, IMP-0287, IMP-0288, IMP-0289.
Open request: LANE_REQUESTS/L-A-1-001.md; ERR-CL-016 PROPOSED.
IMP-0298 separately blocked on DetectionSet.

Baseline: uv sync --frozen --extra persistence --extra crypto passed.
Python 3.12.14, uv 0.12.23. Use .venv and PYTHONUNBUFFERED=1 for child crash markers.
The restrictive command sandbox masks /tmp/.git and blocks socketpair/loopback;
run the unchanged full suite via the supported escalated executor. Do not weaken
source-tree or network assertions. uv needs its existing user cache writable.
Corrected baseline: 2023 passed, 4 skipped; all seven quality gates pass (exit 0).
Initial restricted run: 38 failed, 1744 passed, 6 skipped, 239 errors (exit 1).
Unsandboxed buffered run: 1 failed, 2022 passed, 4 skipped (exit 1).
Unbuffered execution fixed the missing stdout marker before os._exit(9).

NOT_RUN: Python 3.11 check (not installed), three opt-in network checks; remaining
20 leased packets, RFC cryptographic vectors, independent review, CI, TIIR module
acceptance, R1–R23, real keys/model/dataset processing. No keys were generated.
Private packs, hashes and raw execution logs are outside the repository.

IMP-0284 full validation: 2038 passed, 4 skipped (exit 0); detailed quality gate
complete=true, all seven PASS (exit 0). One intermediate summary-only quality run
reported pytest FAIL; its detailed failure was not retained by the human-format
runner. Subsequent standalone and detailed runs passed; the intermediate cause is
not established. No tests or dependencies were changed to obtain those passes.
Lint repairs: preserve source-defined exception names with reasoned N818 comments;
use tuple parametrize names; wrap an overlong test line. mypy strict passes.

IMP-0286: implemented Trusted key identity vs kid; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0286: implemented Trusted key identity vs kid; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0287: implemented Ed25519 key-purpose profile; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0287: implemented Ed25519 key-purpose profile; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0287: implemented Ed25519 key-purpose profile; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0288: implemented Deterministic CBOR encoder profile; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0289: implemented COSE_Sign1 protected-header profile; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.

IMP-0290: implemented Original input byte digest; independent of receipt/stream IDs.
RED recorded before implementation. Mutation 1/1 killed and restored.
