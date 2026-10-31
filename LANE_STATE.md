# Codex B / L-B-1

Base: 2a5db1a39402d8285ce48da71cd3436bce0db91a
Branch: codex-b/B1-eval-foundation
Done: IMP-0396..0420, 25 authored packet commits, all contingent; no independent acceptance claim.
In progress: none in this lease.
Next 5 (future lease only): IMP-0421 generator interface; IMP-0422 clean baseline; IMP-0423 label flip; IMP-0424 annotation suppression; IMP-0425 duplicate flood.

Author evidence: all seven quality gates PASS; separate regression 2205 passed / 4 skipped; evaluator suite 185 passed. Commands use the frozen uv extras, Python 3.12, PYTHONUNBUFFERED=1 and private TMPDIR with supported cache/socket access. Do not modify the repository or run concurrent suites during tests.
Mutations: 25/25 selected decisive mutants killed, no selected survivors/equivalents; exact edits and evidence in each packet's commit body. This is not exhaustive mutation coverage.
Review: early four and final five Important findings reproduced and fixed with executable regressions. Final visible API intentionally refuses unguarded third-party JSON adapters; guarded native/JSON loaders and JCS exports remain available.
Requests: ERR-CL-014/015 remain PROPOSED; ERR-CL-016 in LANE_REQUESTS/B1-001.md and ERR-CL-017 in LANE_REQUESTS/B1-002.md remain PROPOSED. Principal/custodian ratification and downstream validation/interop remain outstanding.
Delivery: format-patch fallback because the existing GitHub API request returned Forbidden. No merge or push to main.
NOT_RUN: real Blind/Final populations and keys, R1-R23 campaigns, upstream independent acceptance, physical mount/IPC isolation, actual runner/web/container release scans, cross-lane registry integration, second host, GitHub CI, fresh-task restoration and certification. Only synthetic data/generated test keys were exercised.
The source packs and private evidence remain outside this checkout. Reusable setup/start instructions are saved in the environment draft; publication is a separate user operation.
