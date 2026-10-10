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

from autowfbench.composer.n8n import ALLOWED, N8nRuntime, validate_workflow
from autowfbench.core.common import ROOT, background_server, digest, http_json, read_json, save_json
from autowfbench.core.contracts import load_challenge
from autowfbench.interfaces.solution import handler_for as solution_handler
from autowfbench.runtime.engine import Engine, EnvironmentProcess
from autowfbench.runtime.judge import Judge, handler_for as judge_handler

MODEL = "gpt-6.1-sol"
PROMPT = """Compose an efficient native n8n workflow for the supplied task and public simApp APIs.
Return only workflow_json, a JSON string containing {name,nodes,connections,settings}.
Do not use tools, inspect local files or seek evaluator internals. No canned task solution is provided.
Read runtime facts; never hard-code customers, IDs, amounts, source code or outcomes.
Treat the documented response fields as authoritative. Access them directly;
do not build fuzzy key matching, recursive policy parsers or compatibility shims.
Use the smallest graph and transformations that implement the documented contract.
Every business action must use an HTTP Request node. Code nodes may transform data only.
Handle ok:false explicitly: retry only retryable writes with the SAME idempotency key,
at most three attempts. Ambiguous writes may already have committed. Read back state.
Permanent errors: do not retry, do not perform dependent writes, report the block honestly.
Do not claim successful actions before checking their actual result. One failed task does
not justify inventing a completed submission. Keep work, code and final explanations concise.

Runtime: n8n 2.42.3. Allowed node types/versions: ALLOWED_NODES.
Each node has a unique id/name, type n8n-nodes-base.<type>, typeVersion, position:[x,y], parameters.
Exactly one Manual Trigger -> Set v3.4 named Context -> generated business graph.
Context parameters are injected by the runner: {run_id,challenge,environment:{base_url,access_token},limits}.
Use $('Context').first().json for stable context after HTTP responses replace the current item.
The terminal node MUST be named Submit, emit exactly one item with the submission schema below,
and execute on all handled success/failure paths. No disconnected nodes, credentials,
pinData, workflow static state, $env, external services, shell, file access or automatic node retries.
Set v3.4: parameters {mode:'raw', jsonOutput:'={{ ...object expression... }}', options:{}}.
Code v2: parameters {mode:'runOnceForAllItems',jsCode:'...return [{json: object}];'}.
Code can access $input.first().json and $('Earlier Node').first().json; use .isExecuted
before referencing a node on a branch that might not run. Avoid Merge nodes that wait for
mutually exclusive branches. A simple linear graph of reads, transforms and conditional
writes is preferable to brittle expressions. Use native If nodes for conditional calls.
If v2.2 boolean condition example: {conditions:{options:{caseSensitive:true,leftValue:'',
typeValidation:'strict',version:2},conditions:[{id:'check',leftValue:'={{ $json.ready }}',
rightValue:true,operator:{type:'boolean',operation:'true',singleValue:true}}],combinator:'and'},options:{}}.
HTTP v4.2 parameters: {method:'POST',url:EXACT_URL,sendHeaders:true,
headerParameters:{parameters:[{name:'Authorization',value:EXACT_AUTH}]},sendBody:true,
specifyBody:'json',jsonBody:'={{ ...argument object... }}',options:{}}.
EXACT_URL must be the exact expression ={{ $('Context').first().json.environment.base_url + '/apps/APP/OPERATION' }}
using a documented path. EXACT_AUTH is ={{ 'Bearer ' + $('Context').first().json.environment.access_token }}
Business errors use HTTP200: inspect $json.ok and $json.error.retryable. Success data is $json.value.
A response value can be an array; preserve the enclosing {ok,value} object.
connections shape: {sourceName:{main:[[{node:targetName,type:'main',index:0}]]}}.
If nodes use main[0] for true and main[1] for false. Branches converge directly into
the next node, not a Merge that waits on both. Use bounded explicit retry branches.

PUBLIC_INPUT:
"""


def public_input(challenge_id):
    # Acquire the same catalog a solution can discover; no fixture state or scorecard.
    process = EnvironmentProcess(challenge_id, 0)
    try:
        apps = http_json(process.public_url + "/apps", token=process.run_token)
    finally:
        process.close()
    return {"challenge": load_challenge(challenge_id)["definition"], "apps": apps, "submission_schema": read_json(ROOT / "benchmark/schemas/submission.schema.json")}


def generate(public, directory, previous=None, feedback=None):
    directory.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT.replace("ALLOWED_NODES", json.dumps(ALLOWED)) + json.dumps(public)
    if previous is not None or feedback:
        prompt += "\nRepair the previous candidate using only these development observations. Return the full workflow.\n" + json.dumps({"previous":previous,"feedback":feedback})
    prompt_path = directory / "prompt.txt"
    if (directory / "response.json").exists():
        if prompt_path.read_text() != prompt:
            raise ValueError("Existing generation used a different prompt; use a fresh output directory")
        workflow = json.loads(read_json(directory / "response.json")["workflow_json"])
        save_json(directory / "workflow.json", workflow)
        return validate_workflow(workflow, public["apps"])
    prompt_path.write_text(prompt)
    schema = {"type":"object", "properties":{"workflow_json":{"type":"string"}}, "required":["workflow_json"], "additionalProperties":False}
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
    workflow = json.loads(read_json(directory / "response.json")["workflow_json"])
    save_json(directory / "workflow.json", workflow)
    return validate_workflow(workflow, public["apps"])


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
            feedback = {"validation_error":str(exc)}
            save_json(root / "validation-error.json", feedback)
            if (root / "workflow.json").exists():
                previous = read_json(root / "workflow.json")
    if best is None:
        raise RuntimeError("No scored candidate; all attempts retained in " + str(directory))
    return best
