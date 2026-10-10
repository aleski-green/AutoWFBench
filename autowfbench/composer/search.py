"""Generate native workflows using public contracts; repair on development runs only."""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autowfbench.composer.n8n import N8nRuntime
from autowfbench.composer.graph import SCHEMA, compile_graph
from autowfbench.core.common import ROOT, background_server, digest, http_json, read_json, save_json
from autowfbench.core.contracts import load_challenge
from autowfbench.interfaces.solution import handler_for as solution_handler
from autowfbench.runtime.engine import Engine, EnvironmentProcess
from autowfbench.runtime.judge import Judge, handler_for as judge_handler

MODEL = "gpt-6.1-sol"
PROMPT = """Compose the smallest reliable native n8n workflow for this task and the public simApp APIs.
Return a typed graph {name,nodes}; the harness compiles only n8n boilerplate, never business logic.
Do not call tools or inspect files. The supplied development preview is read-only example data;
your workflow MUST re-read current data at execution. Never hard-code customer facts, source
code, IDs, amounts or outcomes. Use documented field names directly; no fuzzy schema parsers.
Every business action is an HTTP node. Code nodes only transform data, decide and report.

Each node has name, kind (http/code/if), operation, code, expression, next, on_false.
Unused string fields are empty strings and unused edge lists are []. All fields are required.
The compiler supplies Start -> Context -> your first node. Context contains
{run_id,challenge,environment:{base_url,access_token},limits}. Do not define Start or Context.
Connections use node names: next is the single successor (true branch for If), on_false
is the false successor for If only. Multiple paths can converge directly into a node.
Every handled path must reach a terminal Code node named Submit, with next:[],on_false:[].

HTTP: operation is one documented operation. expression is an n8n object expression,
e.g. ={{ {} }} for no arguments, or ={{ {changes:$('Assess').first().json.changes,
idempotency_key:$('Context').first().json.run_id+':update'} }}. Headers and URL are supplied
by the compiler. A response replaces the current item and has {ok:true,value} or
{ok:false,error:{code,message,retryable}}; value can itself be an array.
Code: code is JavaScript returning [{json: object}]. Access current data with
$input.first().json and prior outputs with $('Node Name').first().json. Check
$('Node Name').isExecuted before referencing a node on a branch that may not run.
If: expression must evaluate to a boolean, e.g. ={{ $json.ok === true }}.
No shell, filesystem, imports, require, $env, external services, static state or credentials.

Inspect every action result. Retry only retryable errors, at most three total attempts,
using the SAME idempotency key and arguments. Ambiguous writes may already have committed.
Read back and compare state before dependent actions or success claims. Permanent failures:
stop dependent writes, verify unchanged state, report the block honestly and perform any
independent communication the task requires. When verification tests are available, run them before the write attempt and again after
the attempt sequence, including a blocked sequence. Explain
business next steps in plain language, never internal status codes alone. No invented success.
Optimize calls and transformation code. Use the actual public policy and observed test/source
results for decisions; do not add speculative compatibility rules. Output the full graph.

PUBLIC_INPUT:
"""


def public_input(challenge_id):
    # Acquire public docs and read-only development observations; never grader internals.
    process = EnvironmentProcess(challenge_id, 0)
    try:
        apps = http_json(process.public_url + "/apps", token=process.run_token)
        preview = [{"operation":op["operation"], "result":http_json(process.public_url+op["path"], {}, process.run_token)} for op in apps["operations"] if op["operation"].endswith(".read")]
    finally:
        process.close()
    return {"challenge": load_challenge(challenge_id)["definition"], "apps": apps, "development_preview":preview, "submission_schema": read_json(ROOT / "benchmark/schemas/submission.schema.json")}


def generate(public, directory, previous=None, feedback=None):
    directory.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT + json.dumps(public)
    if previous is not None or feedback:
        prompt += "\nRepair using these development observations. Return the full typed graph, not raw n8n JSON.\n" + json.dumps({"previous_native_workflow":previous,"feedback":feedback})
    prompt_path = directory / "prompt.txt"
    if (directory / "response.json").exists():
        if prompt_path.read_text() != prompt:
            raise ValueError("Existing generation used a different prompt; use a fresh output directory")
        workflow = compile_graph(read_json(directory / "response.json"), public["apps"])
        save_json(directory / "workflow.json", workflow)
        return workflow
    prompt_path.write_text(prompt)
    schema = SCHEMA
    save_json(directory / "schema.json", schema)
    binary = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not binary:
        raise ValueError("Set CODEX_BIN to the authenticated Codex CLI")
    command = [binary, "exec", "--ignore-user-config", "--model", MODEL, "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "-c", 'model_reasoning_effort="medium"', "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.multi_agent=false", "-c", "web_search=\"disabled\"", "--output-schema", str((directory / "schema.json").resolve()), "--json", "-o", str((directory / "response.json").resolve()), "-"]
    env = {k:v for k,v in os.environ.items() if not k.startswith(("AWB_", "AUTOWFBENCH_"))}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="composer-") as cwd, (directory / "events.jsonl").open("w") as out, (directory / "stderr.log").open("w") as err:
        proc = subprocess.run(command, input=prompt, text=True, cwd=cwd, env=env, stdout=out, stderr=err, timeout=600)
    save_json(directory / "provenance.json", {"model":MODEL,"prompt_digest":digest(prompt),"seconds":round(time.monotonic()-started,3),"returncode":proc.returncode})
    if proc.returncode:
        raise RuntimeError("codex exec failed; see " + str(directory / "stderr.log"))
    workflow = compile_graph(read_json(directory / "response.json"), public["apps"])
    save_json(directory / "workflow.json", workflow)
    return workflow


def evaluate(workflow, challenge_id, seeds, directory, runtime, judge_model=MODEL, repeats=1):
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    judge = background_server(judge_handler(Judge("codex", judge_model, directory / "judge", timeout=300), token)) if judge_model else None
    def execute(request, cancel):
        return runtime.execute(workflow, request, cancel, directory / "n8n" / request["run_id"])
    solution = background_server(solution_handler(executor=execute))
    manifest = {"id":"sol61-composer", "name":"Sol 6.1 composer", "version":digest(workflow)[:12], "runtime":"n8n-2.42.3", "description":"Generated from task and public simApps", "auth_env":"", "endpoint":f"http://127.0.0.1:{solution.server_port}"}
    engine = Engine(directory / "runs", f"http://127.0.0.1:{judge.server_port}" if judge else None, token)
    results, feedback = [], []
    def run_case(case):
        seed, repeat = case
        rid = engine.submit(challenge_id, manifest, seed, background=False)
        result = engine.read(rid)
        result["repeat"] = repeat
        log_path = engine.directory(rid) / "run-log.json"
        log = read_json(log_path) if log_path.exists() else {}
        observations = [e["data"] for e in log.get("events",[]) if e["source"] == "environment" or e["kind"] == "execution_error"]
        visible = {"seed":seed,"score":result["score_0_10"],"status":result["status"],"observations":observations,"submission":log.get("submission")}
        print(json.dumps({"challenge":challenge_id,"seed":seed,"repeat":repeat,"status":result["status"],"score":result["score_0_10"],"calls":result.get("tool_calls"),"seconds":result.get("duration_seconds")}), flush=True)
        return result, visible
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for result, visible in pool.map(run_case, [(seed, repeat) for seed in seeds for repeat in range(repeats)]):
                results.append(result)
                feedback.append(visible)
    finally:
        solution.shutdown(); solution.server_close()
        if judge:
            judge.shutdown(); judge.server_close()
    scores = [r["score_0_10"] for r in results]
    complete = all(s is not None for s in scores) and bool(scores)
    summary = {"challenge":challenge_id,"workflow_digest":digest(workflow),"seeds":list(seeds),"repeats":repeats,"score":min(scores) if complete else None,"mean":round(sum(scores)/len(scores),2) if complete else None,"score_rule":"minimum across all cases and repeats; failures retained; missing judge means unscored", "runs":results}
    save_json(directory / "summary.json", summary)
    return summary, feedback


def compose(challenge_id, directory, attempts=3, target=9):
    directory = directory.resolve()
    public = public_input(challenge_id)
    save_json(directory / "public-input.json", public)
    runtime = N8nRuntime()
    previous, feedback, best, best_score = None, None, None, -1
    for attempt in range(1, attempts+1):
        root = directory / f"attempt-{attempt}"
        print(f"Composing {challenge_id}, attempt {attempt}", flush=True)
        try:
            previous = generate(public, root, previous, feedback)
            summary, feedback = evaluate(previous, challenge_id, [0, 2, 4], root / "development", runtime)
            score = summary["score"]
            if score is not None and score > best_score:
                best, best_score = previous, score
                save_json(directory / "workflow.json", best)
                save_json(directory / "selection.json", {"attempt":attempt,"development_score":score,"workflow_digest":digest(best)})
            if score is not None and score >= target:
                break
        except (ValueError, KeyError) as exc:
            feedback = {"validation_error":str(exc), "raw_candidate":read_json(root / "response.json") if (root / "response.json").exists() else None, "prior_observations":feedback}
            save_json(root / "validation-error.json", feedback)
            if (root / "workflow.json").exists():
                previous = read_json(root / "workflow.json")
    if best is None:
        raise RuntimeError("No scored candidate; all attempts retained in " + str(directory))
    return best
