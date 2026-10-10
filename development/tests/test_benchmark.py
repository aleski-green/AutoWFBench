from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from autowfbench.core.common import JsonHandler, ROOT, background_server, digest, http_json, read_json
from autowfbench.core.contracts import load_challenge, validate
from autowfbench.runtime.engine import Engine, EnvironmentProcess
from autowfbench.runtime.environment import ChallengeEnvironment, ToolFailure, checkout_program
from autowfbench.interfaces.solution import handler_for as solution_handler, solve
from autowfbench.runtime.judge import Judge, handler_for as judge_handler
from autowfbench.core.scoring import calculate, validate_judgement

CRM = "crm-lead-qualification"
INC = "production-checkout-recovery"


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown(); server.server_close()
        self.temp.cleanup()

    def server(self, handler):
        server = background_server(handler)
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    def manifest(self, endpoint):
        return {"id": "reference", "name": "Reference", "version": "1.0.0", "endpoint": endpoint, "runtime": "scripted-reference", "description": "test", "auth_env": ""}

    def run_reference(self, challenge=CRM, variant="reference", judge=True, seed=0):
        url = self.server(solution_handler(variant))
        judge_url = self.server(judge_handler(Judge("demo", data_dir=self.root / "judge"), "judge-secret")) if judge else None
        engine = Engine(self.root / "runs", judge_url, "judge-secret")
        run_id = engine.submit(challenge, self.manifest(url), seed=seed, background=False)
        result = engine.read(run_id)
        self.assertNotEqual(result["status"], "engine_error", result)
        return engine, run_id, result

    def test_both_challenges_run_through_real_http_and_child_environment(self):
        for challenge in (CRM, INC):
            with self.subTest(challenge=challenge):
                engine, run_id, result = self.run_reference(challenge)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["score_0_10"], 7.32)  # 6 + 4 * .33, no fake real judge
                self.assertTrue(result["execution_pass"])
                self.assertEqual(result["judge"]["mode"], "demo")
                log = read_json(engine.directory(run_id) / "run-log.json")
                validate("run-log", log)
                self.assertEqual({e["source"] for e in log["events"]}, {"engine", "environment", "candidate", "verification"})
                self.assertNotIn("judge-secret", json.dumps(log))
                self.assertEqual(len(log["events"]), len({e["id"] for e in log["events"]}))

    def test_incomplete_workflows_do_not_pass_and_keep_partial_evidence(self):
        for challenge in (CRM, INC):
            with self.subTest(challenge=challenge):
                _, _, result = self.run_reference(challenge, "incomplete")
                self.assertFalse(result["execution_pass"])
                self.assertLess(result["score_0_10"], 7.32)

    def test_absent_judge_keeps_total_null(self):
        _, _, result = self.run_reference(judge=False)
        self.assertEqual(result["status"], "awaiting_llm_judge")
        self.assertEqual(result["deterministic_points"], 6)
        self.assertIsNone(result["score_0_10"])

    def test_failed_judge_never_substitutes_a_score(self):
        class Failure(JsonHandler):
            def route(self, method): self.send(500, {"error": "unavailable"})
        engine = Engine(self.root / "runs", self.server(Failure), "secret")
        rid = engine.submit(CRM, self.manifest(self.server(solution_handler())), background=False)
        result = engine.read(rid)
        self.assertEqual(result["status"], "judge_failed")
        self.assertIsNone(result["score_0_10"])

    def test_rescore_integrity_rejects_modified_logs_and_packages(self):
        engine, rid, _ = self.run_reference(judge=False)
        path = engine.directory(rid) / "run-log.json"
        original = path.read_text()
        log = json.loads(original); log["duration_seconds"] = 0
        path.write_text(json.dumps(log))
        with self.assertRaisesRegex(ValueError, "integrity"):
            engine.rescore(rid)
        path.write_text(original)
        p = engine.directory(rid) / "package.json"
        pkg = read_json(p);pkg["scorecard"]["criteria"][0]["weight"] = 9;p.write_text(json.dumps(pkg))
        with self.assertRaisesRegex(ValueError, "integrity"):
            engine.rescore(rid)

    def test_judge_rejects_missing_duplicate_wrong_run_and_unknown_evidence(self):
        engine, rid, _ = self.run_reference(judge=False)
        log = read_json(engine.directory(rid) / "run-log.json")
        package = read_json(engine.directory(rid) / "package.json")
        reply = Judge("demo", data_dir=self.root / "direct-judge").evaluate({"run_log": log, "definition": package["definition"], "scorecard": package["scorecard"]})["judgement"]
        for mutation in (lambda r:r.update(run_id="wrong"), lambda r:r["criteria"].pop(), lambda r:r["criteria"].append(r["criteria"][0]), lambda r:r["criteria"][0].update(evidence_refs=["invented"])):
            value = copy.deepcopy(reply); mutation(value)
            with self.assertRaises(ValueError): validate_judgement(log, package["scorecard"], value)

    def test_codex_judge_invokes_separate_subprocess_and_preserves_provenance(self):
        engine, rid, _ = self.run_reference(judge=False)
        log = read_json(engine.directory(rid) / "run-log.json")
        package = read_json(engine.directory(rid) / "package.json")
        request = {"run_log": log, "definition": package["definition"], "scorecard": package["scorecard"]}
        response = Judge("demo", data_dir=self.root / "template").evaluate(request)["judgement"]
        def fake_run(command, **kwargs):
            self.assertEqual(command[1], "exec")
            self.assertIn("--output-schema", command)
            self.assertIn("--model", command)
            self.assertEqual(kwargs["env"].get("AWB_JUDGE_TOKEN"), None)
            Path(command[command.index("-o")+1]).write_text(json.dumps(response))
            return subprocess.CompletedProcess(command, 0)
        with patch("autowfbench.runtime.judge.shutil.which", return_value="/fake/codex"), patch("autowfbench.runtime.judge.subprocess.run", side_effect=fake_run) as runner:
            result = Judge("codex", "test-model", self.root / "codex-judge").evaluate(request)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(result["provenance"]["mode"], "codex")
        self.assertEqual(result["provenance"]["run_log_digest"], digest(log))

    def test_no_manual_scores_or_forged_provenance_in_submission(self):
        submission = {"protocol_version": "1.0", "run_id": "test", "status": "completed", "final_answer": "done", "artifacts": [], "trace": []}
        for key in ("score", "events", "checks", "duration_seconds"):
            with self.assertRaises(Exception): validate("submission", {**submission, key: 10})

    def test_environment_credentials_and_freeze(self):
        p = EnvironmentProcess(CRM, 0)
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                http_json(p.public_url + "/admin/finalize", {}, p.run_token)
            self.assertEqual(caught.exception.code, 401)
            p.finalize()
            result = http_json(p.public_url + "/tools", {"operation": "crm.update", "arguments": {"changes": {"status": "Qualified"}}}, p.run_token)
            self.assertEqual(result["error"]["code"], "RUN_CLOSED")
        finally: p.close()

    def test_seeded_state_isolated_and_all_required_crm_fields_checked(self):
        first, second = ChallengeEnvironment(load_challenge(CRM), 0), ChallengeEnvironment(load_challenge(CRM), 1)
        self.assertNotEqual(first.fixtures["budget_aed"], second.fixtures["budget_aed"])
        first.state["lead"]["status"] = "Qualified"
        self.assertEqual(second.state["lead"]["status"], "New")
        self.assertFalse(first.finalize()["checks"]["correct_lead"])

    def test_checkout_rejects_arbitrary_python_and_baseline_fails(self):
        for source in ("import os\nos.system('echo unsafe')", "def checkout(amount, currency):\n    return eval(amount)"):
            with self.assertRaises(ToolFailure): checkout_program(source)
        env = ChallengeEnvironment(load_challenge(INC))
        self.assertFalse(env.execute("tests.run", {})["value"]["passed"])

    def test_tool_budget_records_failure(self):
        package = load_challenge(CRM);package["definition"]["limits"]["tool_calls"] = 1
        env = ChallengeEnvironment(package)
        self.assertTrue(env.execute("crm.read", {})["ok"])
        self.assertEqual(env.execute("crm.read", {})["error"]["code"], "TOOL_LIMIT")

    def test_timeout_freezes_evidence_and_retains_unscored_run(self):
        class NeverDone(JsonHandler):
            def route(self, method):
                if method == "POST": self.send(202, {"execution_id": "slow"})
                else: self.send(200, {"status": "running"})
        package = load_challenge(CRM)
        package["definition"]["limits"]["wall_clock_seconds"] = .25
        package["hashes"]["definition"] = digest(package["definition"])
        engine = Engine(self.root / "timeout")
        with patch("autowfbench.runtime.engine.load_challenge", return_value=package):
            rid = engine.submit(CRM, self.manifest(self.server(NeverDone)), background=False)
        result = engine.read(rid)
        self.assertEqual(result["termination_reason"], "timeout")
        self.assertFalse(result["execution_pass"])
        self.assertIsNone(result["score_0_10"])

    def test_foreign_run_submission_is_rejected_not_attached_to_evidence(self):
        class ForeignResult(JsonHandler):
            def route(self, method):
                if method == "POST":
                    self.send(202, {"execution_id": "foreign"})
                else:
                    self.send(200, {"status": "completed", "submission": {"protocol_version": "1.0", "run_id": "another-run", "status": "completed", "final_answer": "Forged success", "artifacts": [], "trace": []}})
        engine = Engine(self.root / "foreign")
        rid = engine.submit(CRM, self.manifest(self.server(ForeignResult)), background=False)
        run = read_json(engine.directory(rid) / "run-log.json")
        self.assertEqual(run["termination_reason"], "protocol_error")
        self.assertIsNone(run["submission"])
        self.assertFalse(any(e["source"] == "candidate" for e in run["events"]))


if __name__ == "__main__": unittest.main()
