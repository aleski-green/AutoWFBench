# Architecture

Use rTernarity: split architecture into three meaningful responsibilities, then
repeat only when a responsibility needs subdivision. Functional requirements
take precedence; explain exceptions in the change description. Collections
(challenges, schemas, examples, tests and results) may have any number of leaves.
Keep entrypoints and package markers; do not add wrappers or empty branches to
meet a count.

```text
autowfbench/
  core/                    shared primitives, contracts and scoring
    common.py / contracts.py / scoring.py
  runtime/                 benchmark execution, environment and judging
    engine.py / environment/ / judge.py
      environment: apps.py / state.py / server.py
  interfaces/              CLI, workflow composition and solution protocol
    cli.py / composer/ / solution.py
      composer: graph.py / n8n.py / search.py
```

Dependencies flow from interfaces to runtime to core. Core never imports its
consumers; runtime never imports interfaces. Within the environment, `apps.py`
defines public contracts, `state.py` owns simulation and verification, and
`server.py` handles transport. State may use contracts; neither imports transport.
The graph compiler uses n8n validation; the n8n adapter never imports search or
generation. Keep the import graph acyclic, including imports inside functions.

Preserve CLI commands, HTTP contracts and benchmark behavior during refactors.
Internal Python import paths may change; update callers directly. Run
`python -m unittest discover -s development/tests -v` and
`python development/tools/verify_lock.py`. Architecture tests enforce only the
explicit splits above, dependency direction and import cycles.
