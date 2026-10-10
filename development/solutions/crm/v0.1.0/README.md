# CRM Lead Qualification — v0.1.0 baseline

This directory contains the first AutoWFBench-compatible baseline for the CRM challenge.

## Boundary

The candidate solution does **not** initialize the benchmark environment, inspect the scorecard, judge itself, or calculate a score. Every CRM/customer/research/follow-up action goes through the run-scoped environment URL and access token supplied by AutoWFBench.

## Run locally

From the repository root with the package installed:

```powershell
python -m pip install -e .
python development/solutions/crm/v0.1.0/adapter.py --port 9301
```

In another terminal, run AutoWFBench against the manifest:

```powershell
python -m autowfbench run crm-lead-qualification development/solutions/crm/v0.1.0/manifest.json
```

For an official scored experiment, start the independent Codex judge described in the root README and pass its judge URL to the benchmark command. Do not treat a run without the real judge as an official score.

## Baseline behavior

The baseline reads the inquiry, service/policy documents, and company research; asks the customer for qualification facts; updates the CRM with one retry for the injected transient failure; creates the discovery follow-up; sends the customer response; and reads the CRM back before reporting completion.

This version intentionally establishes behavior before the speed optimization. v0.2.0 will test replacing deterministic LLM decisions with rule-based logic where appropriate while retaining LLM reasoning only where it is needed.
