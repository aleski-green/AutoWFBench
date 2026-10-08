# Realistic checkout environment milestone

This milestone adds `production-checkout-recovery-realistic`. The existing
`production-checkout-recovery` simulator, Composer repository, and n8n bridge
are not changed.

## Ownership and boundary

AutoWFBench creates a fresh temporary workspace in each environment process,
copies seven public assets from its own challenge package, and removes that
workspace when the process closes. It never reads the standalone Desktop
application at run time. Source and evidence access uses an explicit allowlist;
there is no shell, arbitrary path, or verifier tool. Only the body of
`checkout` in `app.py` can be patched. Supporting modules and public tests are
immutable. The summary is the only additional writable artifact.

`ManagedEnvironment` records validated tool calls, failures, patch hashes, and
snapshots, enforces the tool budget, and freezes evaluation exactly once.
`CheckoutAdapter` implements the application-specific operations. The existing
HTTP handler continues serving POST `/tools` with the run token and POST
`/admin/finalize` with the distinct controller token.

Application execution uses a disposable Docker container for each operation.
The workspace and public execution worker are read-only mounts. Containers run
without networking, host credentials, a Docker socket, controller repository,
scorecards, or verification code. They have a non-root user, dropped capabilities,
a read-only root filesystem, bounded scratch space, CPU/memory/PID/output limits,
and a deadline. There is no host-Python fallback. Code edits persist in the
run workspace; temporary execution-process state does not persist between calls.

The published Python image is pinned to an OCI index digest. Preflight requires
the daemon and pre-provisioned image, resolves the local immutable image ID, and
records that ID in protected verification evidence. Execution never implicitly
pulls an image. Reads and controlled patches remain available if Docker is down;
execution returns an explicit infrastructure error.

The controller verifier computes private expected charges and compares actual
application responses. It separately checks invalid-order rejection, immutable
assets, allowed patch scope, the failing-test/patch/passing-test sequence, and
summary sections. It does not trust candidate claims or public test success as
proof of application correctness. Expected values and verifier implementations
are never mounted in the application container. Test inputs necessarily reach
the application during protected evaluation, after the run has been frozen.

The security boundary is the tool API and application container. A human or
process already authorized to read the controller's host repository is outside
this threat model; this milestone does not sandbox the user's entire desktop.

## Operations

| Operation | Arguments | Effect |
|---|---|---|
| `capabilities.list` | `{}` | Discover public JSON schemas and effect classifications |
| `evidence.read` | `path` | Read logs, failed orders, or deployment diff |
| `source.read` | `path` | Read one public source/test file |
| `checkout.patch` | `old`, `new` | Apply a unique-fragment patch to the checkout body |
| `tests.run` | `{}` | Run the immutable public unit tests in Docker |
| `checkout.observe` | `order` | Return the actual application response from Docker |
| `artifact.write` | `content` | Write `incident_summary.md` |

All argument objects reject undeclared properties. Successful patch persistence
is not treated as successful execution. The final evidence includes an explicit
`verification_available` check and diagnostic details for infrastructure errors.
Unavailable execution cannot pass the benchmark. Pending LLM criteria remain
null using the existing scoring implementation; no LLM judge is required for
this milestone.

## Commands

Run from the repository root:

```sh
cd /Users/harishmasabu/Desktop/AutoWFBench
docker info --format '{{.ServerVersion}}'
docker pull python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
.venv/bin/python -m unittest discover -s development/tests -v
AWB_TEST_DOCKER=1 .venv/bin/python -m unittest development.tests.test_realistic_checkout.DockerIntegrationTests -v
.venv/bin/python -m development.tools.demo_realistic_checkout --data-dir runs/realistic-environment-milestone
.venv/bin/python development/tools/verify_lock.py
```

The regular suite explicitly skips the three real-container tests unless
`AWB_TEST_DOCKER=1`. With that flag, missing Docker is a failure, not a skip.
Mocked sandbox tests exercise adapter and verifier contracts only; they do not
establish that the real application executed successfully.

The demonstration starts the existing Engine and its run-scoped environment
process. A labeled scripted smoke client inspects evidence and source, requests
baseline tests, submits a source-derived patch, reads the changed source,
requests tests and a checkout probe, and writes an honest summary. Engine
finalization independently freezes and evaluates the state. The script writes
`demonstration.json` plus the normal run log and scorecard and exits nonzero if
execution is not verified. It is not a Composer or n8n performance claim.

## Baseline integrity review (lock intentionally unchanged)

Only two already-protected implementation files change:

- `autowfbench/runtime/environment.py`: select the managed adapter for the new
  implementation and clean up application resources on environment termination.
- `autowfbench/core/contracts.py`: bind packaged application assets, new adapter
  code, worker, and protected verifier code into the environment package hash.

A future owner-approved lock update must rehash those two files and ADD entries
for all new runtime adapter/verifier Python modules and the new challenge's
three JSON files and seven assets. Retain all existing entries, especially the
legacy challenges, schemas, engine, judge, and scoring implementation. Do not
remove a failed check or weaken its expected digest. Existing challenge JSON,
scoring, and judge behavior remain unchanged. Hashes of existing run packages
change when their shared environment implementation changes, as they did before.

The unchanged lock is expected to report exactly those two protected-file
mismatches. Passing functional regressions is not a substitute for reviewing
and explicitly approving the new trusted baseline. Docker recovery, isolation,
and resource-limit tests must pass before declaring this milestone fully
validated or approving that baseline.
