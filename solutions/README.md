# Production Checkout Recovery — Solution Iterations

This directory contains three iterations of the Production Checkout Recovery solution evaluated with AutoWFBench and the external Codex judge.

## Results

| Version | Purpose | Score | Execution Time |
|---|---|---:|---:|
| v0.1.0 | Baseline autonomous recovery workflow | 8.00/10 | 13.07s |
| v0.2.0 | Add observable pre-repair and post-repair verification | 10.00/10 | 21.15s |
| v0.3.0 | Reduce unnecessary Codex dependency and execution overhead | 9.33/10 | 7.59s |

## v0.1.0 — Baseline

Implements the complete checkout recovery flow:

- read incident evidence
- inspect checkout source
- diagnose the failure using Codex
- apply the patch
- run verification
- generate an incident summary
- submit the result through the AutoWFBench Solution API

The solution scored 8/10. The repair itself was correct, but verification was only observed after the repair. As a result, the benchmark could not verify the complete failing-test → repair → passing-test recovery sequence.

## v0.2.0 — Recovery Verification

Adds explicit verification before and after the repair.

This allows the benchmark to observe the failing state, the repair, and the successful recovered state.

Result: 10/10.

The trade-off was increased execution time compared with the baseline.

## v0.3.0 — Efficiency Experiment

Explores reducing unnecessary Codex dependency while preserving the recovery behavior.

The solution retained all deterministic recovery checks and reduced execution time from 21.15s in v0.2.0 to 7.59s.

Result: 9.33/10.

The remaining score reduction came from the communication criterion, where the final answer was less explicit even though the recovery itself remained correct.

## Structure

Each version contains:

- `adapter.py` — AutoWFBench Solution API adapter
- `checkout-vX.X.X.json` — solution manifest
- `workflow.json` — exported n8n workflow

## Next Steps

Further benchmark work will explore additional evaluation dimensions such as tool-call efficiency, retries, and recovery quality, informed by Harbor and Terminal-Bench evaluation approaches.


