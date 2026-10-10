# CRM v0.3.0 — simplification

Goal: simplify the v0.2 candidate without sacrificing benchmark quality or its reduced tool-call count.

This version keeps the eight-call workflow from v0.2 and simplifies the CRM retry control flow: perform the update, retry once only when the environment marks the failure retryable, then use one final failure check.

No benchmark definition, environment, scorecard, verifier, evidence collection, or judge behavior is changed.

Expected behavior: 8 tool calls, full recovery/readback, and the same required customer/follow-up outcomes. Score and execution time must be measured by AutoWFBench.

Run locally:

```cmd
.venv\Scripts\python.exe development\solutions\crm\v0.3.0\adapter.py --port 9303
```
