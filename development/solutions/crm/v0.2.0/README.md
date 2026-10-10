# CRM v0.2.0 — efficiency

Goal: preserve benchmark quality while reducing unnecessary environment work.

Compared with v0.1.0, this version removes `research.read`. The qualification decision does not depend on company-profile research: the inquiry supplies the lead/contact, `documents.read` supplies qualification and communication policy, and `customer.ask` supplies the required qualification facts.

The retrying `crm.update`, `crm.read` readback, `followup.create`, and `customer.send` calls remain because they provide required state changes, recovery behavior, verification, and customer contact.

Expected tool-call count: 8 instead of the v0.1 baseline's 9. Benchmark score and timing must be measured by AutoWFBench; this solution does not grade itself.

Run locally:

```cmd
.venv\Scripts\python.exe development\solutions\crm\v0.2.0\adapter.py --port 9302
```
