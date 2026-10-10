# AutoWFBench v2

Sol 6.1 composes native n8n workflows for two stateful simApps challenges:
CRM qualification and checkout repair. One composer; no task-specific templates.

Requires Python 3.11+, Node 22+, authenticated Codex and n8n **2.42.3**.

```sh
pip install -e .
npm install --prefix .runtime n8n@2.42.3
export N8N_BIN="$PWD/.runtime/node_modules/.bin/n8n"
# Optional: export CODEX_BIN=/path/to/working/codex
python -m autowfbench compose all --data-dir runs/composer
```

Generation uses `codex exec --model gpt-6.1-sol`, public tasks/API docs, read-only
development observations and at most
three candidates. Each is tested on development seeds 0, 2, 4. Numeric scores and
public action traces inform repairs. The best candidate is frozen, then run on
held-out seeds 6–11 twice. No hold-out feedback enters generation. Each generation,
workflow, execution, model response and judge decision is retained in `runs/`.
The same workflow is reused without edits across a challenge's cases.

```sh
python -m autowfbench evaluate crm-lead-qualification path/to/workflow.json
python -m unittest discover -s development/tests -v
python development/tools/verify_lock.py
```

Score: 7 points for verified task execution, 1 for efficiency, 2 from an independent
Codex judge session for evidence, communication and honesty. Yes=1, maybe=.33, no=0.
A critical failure or false success caps the score at 7.99. Missing judging leaves
the score null. The suite score is the **minimum** across all cases/repeats; the
mean is also reported. **8: useful, verified result; 9: almost perfect; 10: every
criterion on every tested case.** These are rubric targets, not a calibrated claim
about arbitrary real-world work. V1 and v2 scores are not comparable.

Efficient budgets: CRM 9 calls (7 if writes are permanently blocked), checkout 6;
one extra call when a retryable failure is injected. Cold n8n startup is measured
and reported separately from business call count; no hardware-dependent speed
points. Retries must reuse idempotency keys. A blocked scenario requires preserved
state, bounded attempts and an honest, actionable explanation.

Each run has fresh simulated CRM, messaging, policy/research, repository, tests and
incident apps as applicable. Discover their short docs and typed request/result
schemas with authenticated `GET /apps`; call `POST /apps/{app}/{operation}` using
the run-scoped Bearer token and argument object. Responses are `{ok:true,value}` or
`{ok:false,error:{code,message,retryable}}`. `POST /tools` is the compatibility route.
No admin API or evaluator checks are exposed to the composer.

Cases cover healthy execution, transient failure/alternate currency, negative
qualification/both currencies, missing facts/transient patch, permanent denial,
and ambiguous committed writes. Held-out cases change identities, amounts, CRM,
languages and source formatting. Checkout interprets a restricted Python AST;
this is not a full software-repair benchmark or a simulation of every SaaS API.

The engine freezes authoritative events before a separate judge grades them.
A typed graph avoids JSON-inside-JSON generation errors. A small compiler supplies
native node boilerplate; Sol generates all decisions and expressions. The n8n
adapter injects run context only; HTTP nodes perform business actions.
Code nodes transform data. Local n8n runs use fresh storage, restricted nodes,
redacted logs and process-group cancellation. This is not a hostile-code sandbox.
Use an isolated machine/container for third-party workflows.

External solution adapters remain supported: `POST /runs` -> `{execution_id}`,
`GET /runs/{id}` -> status + `submission.schema.json`, `POST /runs/{id}/cancel`.
Run `python -m autowfbench --help` for judge, environment and adapter commands.
The scripted example adapter is a protocol/test fixture, not a composer result.
Benchmark edits require an owner-approved version and lock update.

Code follows [rTernarity](CONTRIBUTING.md): core contracts, benchmark runtime and
interfaces, recursively split by responsibility.
