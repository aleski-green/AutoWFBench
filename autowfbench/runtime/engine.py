"""Generic orchestration: the engine never chooses a candidate's next action."""
from __future__ import annotations

import json
import os
import secrets
import selectors
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

from autowfbench.core.common import ROOT, digest, http_json, now, read_json, save_json
from autowfbench.core.contracts import load_challenge, validate
from autowfbench.core.scoring import calculate


def redact(value, secrets_to_remove=()):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in ("access_token", "authorization", "api_key", "password") else redact(v, secrets_to_remove) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets_to_remove) for v in value]
    if isinstance(value, str):
        for token in secrets_to_remove:
            if token:
                value = value.replace(token, "[REDACTED]")
    return value


class EnvironmentProcess:
    def __init__(self, challenge_id, seed, bind_host="127.0.0.1", public_host="127.0.0.1"):
        self.run_token, self.admin_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        env = {k:v for k,v in os.environ.items() if not k.startswith("AWB_")}
        env.update(AWB_RUN_TOKEN=self.run_token, AWB_ENV_ADMIN_TOKEN=self.admin_token, PYTHONPATH=str(ROOT))
        self.proc = subprocess.Popen([sys.executable, "-m", "autowfbench", "environment", challenge_id, "--seed", str(seed), "--host", bind_host], env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=30):
                    raise ValueError("Environment startup timed out")
                line = self.proc.stdout.readline()
            if not line:
                raise ValueError("Environment failed to start: " + self.proc.stderr.read(2000))
            port = json.loads(line)["port"]
            self.admin_url = f"http://127.0.0.1:{port}"
            self.public_url = f"http://{public_host}:{port}"
        except Exception:
            self.close()
            raise

    def finalize(self):
        return http_json(self.admin_url + "/admin/finalize", {}, self.admin_token)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()


class Engine:
    def __init__(self, data_dir=Path("runs"), judge_url=None, judge_token=None, env_bind="127.0.0.1", env_public="127.0.0.1"):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.judge_url, self.judge_token = judge_url, judge_token
        self.env_bind, self.env_public = env_bind, env_public
        self.lock, self.active = threading.RLock(), set()

    def directory(self, run_id):
        if not isinstance(run_id, str) or not run_id.startswith("run-") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in run_id):
            raise ValueError("Invalid run ID")
        return self.data_dir / run_id

    def read(self, run_id):
        return read_json(self.directory(run_id) / "result.json")

    def list_runs(self):
        return sorted([read_json(p) for p in self.data_dir.glob("run-*/result.json")], key=lambda x:x["created_at"], reverse=True)

    def update(self, run_id, **values):
        with self.lock:
            path = self.directory(run_id) / "result.json"
            result = read_json(path)
            result.update(values)
            save_json(path, result)

    def submit(self, challenge_id, solution, seed=0, background=True):
        validate("solution", solution)
        endpoint = urlparse(solution["endpoint"])
        if not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("Solution endpoint must be an HTTP(S) base URL without credentials/query")
        if type(seed) is not int or seed < 0:
            raise ValueError("Seed must be a nonnegative integer")
        package = load_challenge(challenge_id)
        run_id = "run-" + uuid.uuid4().hex
        root = self.directory(run_id)
        with self.lock:
            if len(self.active) >= 4:
                raise ValueError("At most four attempts may execute concurrently")
            self.active.add(run_id)
        save_json(root / "package.json", package)
        save_json(root / "solution.json", solution)
        save_json(root / "result.json", {"run_id": run_id, "created_at": now(), "challenge_id": challenge_id, "challenge_version": package["definition"]["version"], "challenge_hashes": package["hashes"], "solution": {k:solution[k] for k in ("id", "name", "version", "runtime")}, "seed": seed, "status": "queued", "duration_seconds": None, "score_0_10": None, "judge": None})
        if background:
            threading.Thread(target=self._execute, args=(run_id, package, solution, seed), daemon=True).start()
        else:
            self._execute(run_id, package, solution, seed)
        return run_id

    def _execute(self, run_id, package, solution, seed):
        process, execution_id, submission = None, None, None
        endpoint = solution["endpoint"].rstrip("/")
        token = os.environ.get(solution["auth_env"]) if solution["auth_env"] else None
        events, reason = [], "engine_error"
        started, started_at = time.monotonic(), now()
        def event(kind, data):
            events.append({"id": f"engine-{len(events)+1:04d}", "source": "engine", "timestamp": now(), "kind": kind, "data": data})
        try:
            if solution["auth_env"] and not token:
                raise ValueError("Missing solution credential environment variable")
            process = EnvironmentProcess(package["definition"]["id"], seed, self.env_bind, self.env_public)
            # Measured execution excludes environment provisioning and judge latency.
            started, started_at = time.monotonic(), now()
            deadline = started + package["definition"]["limits"]["wall_clock_seconds"]
            event("solution_started", {"limits": package["definition"]["limits"]})
            self.update(run_id, status="running")
            request = {"protocol_version": "1.0", "run_id": run_id, "challenge": package["definition"], "environment": {"base_url": process.public_url, "access_token": process.run_token}, "limits": package["definition"]["limits"]}
            reply = http_json(endpoint + "/runs", request, token, timeout=min(15, deadline-time.monotonic()))
            execution_id = reply.get("execution_id")
            if not isinstance(execution_id, str) or not execution_id or len(execution_id) > 200:
                raise ValueError("Solution must return execution_id")
            event("solution_accepted", {"execution_id": execution_id})
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    break
                response = http_json(endpoint + "/runs/" + quote(execution_id, safe=""), token=token, timeout=min(5, remaining))
                status = response.get("status")
                if status in ("completed", "failed"):
                    candidate_submission = validate("submission", response["submission"])
                    if candidate_submission["run_id"] != run_id or candidate_submission["status"] != status:
                        raise ValueError("Submission run/status mismatch")
                    submission = candidate_submission
                    reason = "completed" if status == "completed" else "solution_failed"
                    break
                if status not in ("queued", "running"):
                    raise ValueError("Unknown solution status")
                time.sleep(min(.15, max(0, deadline-time.monotonic())))
        except Exception as exc:
            reason = "protocol_error" if process else "engine_error"
            if process and time.monotonic() - started >= package["definition"]["limits"]["wall_clock_seconds"]:
                reason = "timeout"
            event("execution_error", {"type": type(exc).__name__, "message": str(exc)[:1000]})
        finished_at, duration = now(), round(time.monotonic() - started, 4)
        try:
            event("solution_stopped", {"termination_reason": reason, "duration_seconds": duration})
            # Freeze immediately, before network cancellation or judging.
            if not process:
                raise ValueError("No environment evidence available")
            evidence = process.finalize()
            if execution_id and reason not in ("completed", "solution_failed"):
                try:
                    http_json(endpoint + "/runs/" + quote(execution_id, safe="") + "/cancel", {}, token, timeout=2)
                except Exception:
                    event("cancellation_unconfirmed", {})
            candidate_events = []
            if submission:
                candidate_events.append({"id": "candidate-final", "source": "candidate", "timestamp": finished_at, "kind": "final_output", "data": {"final_answer": submission["final_answer"], "artifacts": submission["artifacts"]}})
                for i, trace in enumerate(submission["trace"]):
                    candidate_events.append({"id": f"candidate-trace-{i:04d}", "source": "candidate", "timestamp": finished_at, "kind": "reported_trace", "data": trace})
            else:
                event("missing_submission", {"message": "No valid final submission was received"})
            log = {"schema_version": "1.0", "run_id": run_id, "challenge": {"id": package["definition"]["id"], "version": package["definition"]["version"], "hashes": package["hashes"]}, "solution": {**{k:solution[k] for k in ("id", "name", "version", "runtime")}, "manifest_digest": digest(solution)}, "seed": seed, "started_at": started_at, "finished_at": finished_at, "duration_seconds": duration, "termination_reason": reason, "events": [*events, *evidence["events"], *candidate_events, *evidence["verification"]], "snapshots": {"initial": evidence["initial"], "final": evidence["final"]}, "checks": evidence["checks"], "submission": submission}
            log = redact(log, (token, process.run_token, process.admin_token, self.judge_token))
            validate("run-log", log)
            save_json(self.directory(run_id) / "run-log.json", log)
            self.update(run_id, status="awaiting_llm_judge", duration_seconds=duration, termination_reason=reason, run_log_digest=digest(log), tool_calls=evidence["tool_calls"])
            process.close(); process = None
            self.rescore(run_id)
        except Exception as exc:
            save_json(self.directory(run_id) / "engine-failure.json", {"events": redact(events, (token, self.judge_token)), "error": str(exc)[:1000]})
            prior = next((e["data"].get("message") for e in reversed(events) if e["kind"] == "execution_error"), None)
            self.update(run_id, status="engine_error", error=(prior or str(exc))[:1000], duration_seconds=duration, score_0_10=None)
        finally:
            if process:
                process.close()
            with self.lock:
                self.active.discard(run_id)

    def rescore(self, run_id):
        root = self.directory(run_id)
        run, package = read_json(root / "run-log.json"), read_json(root / "package.json")
        if digest(run) != self.read(run_id)["run_log_digest"]:
            raise ValueError("Frozen log integrity check failed")
        if any(digest(package[k]) != run["challenge"]["hashes"][k] for k in ("definition", "environment", "scorecard")):
            raise ValueError("Frozen challenge package integrity check failed")
        response, provenance, error = None, None, None
        if self.judge_url:
            self.update(run_id, status="judging", score_0_10=None)
            try:
                reply = http_json(self.judge_url.rstrip("/") + "/evaluate", {"definition": package["definition"], "scorecard": package["scorecard"], "run_log": run}, self.judge_token, timeout=350)
                provenance = reply["provenance"]
                if provenance["run_log_digest"] != digest(run) or provenance["response_digest"] != digest(reply["judgement"]) or provenance["mode"] not in ("codex", "demo"):
                    raise ValueError("Judge provenance mismatch")
                response = reply["judgement"]
                report = calculate(run, package["scorecard"], response, provenance)
                save_json(root / ("judgement-" + uuid.uuid4().hex + ".json"), reply)
                if response["status"] != "complete":
                    error = "Judge reported incomplete evidence: " + "; ".join(response["issues"])
            except Exception as exc:
                response, provenance, error = None, None, type(exc).__name__ + ": " + str(exc)[:1000]
        report = calculate(run, package["scorecard"], response, provenance, error)
        save_json(root / "scorecard-result.json", report)
        self.update(run_id, **{k:report[k] for k in ("status", "score_0_10", "deterministic_points", "execution_pass", "judge", "judge_error")}, criteria=report["criteria"])
        return report
