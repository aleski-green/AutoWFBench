# AutoWFBench

Two stateful simulation challenges: CRM qualification and checkout repair.
The engine records actions and verifies state. An independent Codex judge grades
explanations. Solutions never supply their own score.

Requires Python 3.11+. Install with `pip install -e .`.

```sh
python -m unittest discover -s development/tests -v
python development/tools/verify_lock.py
```

Authenticate Codex, then run the judge and your solution adapter:

```sh
export AWB_JUDGE_TOKEN='use-a-private-random-token'
python -m autowfbench judge --model gpt-6-astra
python -m autowfbench example-solution
python -m autowfbench run crm-lead-qualification \
  benchmark/examples/solutions/crm-reference.json --judge-url http://127.0.0.1:9100
```

`runs/` preserves the package, manifest, frozen events, verification, and score.
Without a judge, totals stay null. Example adapters are protocol fixtures.

Solution API: `POST /runs` accepts `{run_id, challenge, environment, limits}` and
returns `{execution_id}`. `GET /runs/{execution_id}` returns status and a
`benchmark/schemas/submission.schema.json` submission. `POST /runs/{id}/cancel`
stops execution. Environment actions use bearer authentication on `POST /tools`
with `{operation, arguments}`; responses are `{ok:true,value}` or
`{ok:false,error:{code,message,retryable}}`.

Benchmark changes require a new owner baseline and lock. Old scores are not
comparable after changes. This is a local development benchmark; process
separation is not a security boundary against hostile same-user code.
