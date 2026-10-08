"""Benchmark-owned simulators. Each engine attempt launches a separate process.

No candidate code is evaluated by this service. The checkout fixture interprets
a deliberately small, validated AST; unrestricted Python needs a real sandbox.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import threading
from http.server import ThreadingHTTPServer

from autowfbench.core.common import HTTPError, JsonHandler, now
from autowfbench.core.contracts import load_challenge


class ToolFailure(Exception):
    def __init__(self, code, message, retryable=False):
        self.code, self.message, self.retryable = code, message, retryable


def require(condition, message):
    if not condition:
        raise ToolFailure("INVALID_ARGUMENT", message)


def checkout_program(source):
    """Validate the full program before interpretation, including unused branches."""
    require(isinstance(source, str) and len(source) <= 6000, "Invalid source")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ToolFailure("INVALID_SOURCE", str(exc))
    require(len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef), "One function required")
    fn = tree.body[0]
    require(fn.name == "checkout" and [a.arg for a in fn.args.args] == ["amount", "currency"], "Keep checkout signature")
    require(not (fn.decorator_list or fn.args.defaults or fn.args.kwonlyargs or fn.args.posonlyargs or fn.args.vararg or fn.args.kwarg or fn.returns or fn.type_comment), "Unsupported function definition")
    require(all(a.annotation is None for a in fn.args.args), "Annotations are unsupported")

    def check_return(node):
        require(isinstance(node, ast.Return), "Expected return")
        call = node.value
        require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "charge_card", "Only charge_card calls allowed")
        require(len(call.args) == 2 and not call.keywords, "Two positional arguments required")
        require(all(isinstance(a, ast.Name) and a.id in ("amount", "currency") for a in call.args), "Arguments must reference input values")

    if len(fn.body) == 1:
        check_return(fn.body[0])
    else:
        require(len(fn.body) == 2 and isinstance(fn.body[0], ast.If), "Unsupported checkout structure")
        branch = fn.body[0]
        test = branch.test
        require(isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "currency" and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value in ("EUR", "USD"), "Only a currency comparison is allowed")
        require(len(branch.body) == 1 and not branch.orelse, "Unsupported branch")
        check_return(branch.body[0]); check_return(fn.body[1])
    return fn


def checkout(source, amount, currency):
    fn = checkout_program(source)
    node = fn.body[-1]
    if isinstance(fn.body[0], ast.If) and currency == fn.body[0].test.comparators[0].value:
        node = fn.body[0].body[0]
    values = {"amount": amount, "currency": currency}
    amount, currency = [values[a.id] for a in node.value.args]
    if isinstance(amount, bool) or not isinstance(amount, (float, int)) or amount <= 0 or currency not in ("EUR", "USD"):
        return {"status": 500, "error": "Invalid payment"}
    return {"status": 200, "amount": amount, "currency": currency}


class ChallengeEnvironment:
    def __init__(self, package, seed=0):
        self.package, self.seed = package, seed
        self.kind = package["environment"]["implementation"]
        self.fixtures = copy.deepcopy(package["environment"]["fixtures"])
        self.lock = threading.RLock()
        self.events, self.calls, self.frozen = [], 0, False
        if self.kind == "crm":
            self.fixtures["budget_aed"] += (seed % 5) * 10000
            self.fixtures["volume"] += (seed % 7) * 1000
            self.failures = package["environment"]["failure_injection"]["crm_update_failures"]
            self.state = {"lead": {k: self.fixtures[k] for k in ("lead_id", "company", "contact", "status")}, "messages": [], "followups": [], "customer_replied": False}
        else:
            self.state = {"source": self.fixtures["source"], "patches": []}
        self.initial = copy.deepcopy(self.state)

    def event(self, operation, arguments, result):
        self.events.append({"id": f"env-{len(self.events) + 1:04d}", "source": "environment", "timestamp": now(), "kind": "tool_call", "data": {"operation": operation, "arguments": copy.deepcopy(arguments), "result": copy.deepcopy(result)}})

    def execute(self, operation, arguments):
        with self.lock:
            if self.frozen:
                return {"ok": False, "error": {"code": "RUN_CLOSED", "message": "Run is frozen", "retryable": False}}
            self.calls += 1
            try:
                require(isinstance(arguments, dict), "Arguments must be an object")
                if self.calls > self.package["definition"]["limits"]["tool_calls"]:
                    raise ToolFailure("TOOL_LIMIT", "Tool budget exhausted")
                if operation not in self.package["definition"]["capabilities"]:
                    raise ToolFailure("UNKNOWN_TOOL", operation)
                value = self.crm(operation, arguments) if self.kind == "crm" else self.incident(operation, arguments)
                result = {"ok": True, "value": value}
            except ToolFailure as exc:
                result = {"ok": False, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}
            except (KeyError, TypeError, ValueError) as exc:
                result = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc), "retryable": False}}
            self.event(operation, arguments, result)
            return result

    def crm(self, op, a):
        f = self.fixtures
        if op == "inquiry.read":
            return {"lead_id": f["lead_id"], "contact": f["contact"], "body": "We need WhatsApp customer support automation with escalation to employees. Can you help?"}
        if op == "documents.read":
            return {"qualification": {"minimum_budget_aed": 100000, "maximum_timeline_weeks": 16}, "capabilities": ["WhatsApp", "Arabic", "English", "human_handoff", "Salesforce_subject_to_assessment"], "communication": "Do not guarantee pricing, deployment dates, accuracy, or compliance before discovery. Propose a discovery call when qualified."}
        if op == "research.read":
            return {"company": f["company"], "industry": "Retail", "contact_authority": "Operations Director", "sources": ["fixture/company-profile"]}
        if op == "customer.ask":
            questions = a.get("questions")
            require(isinstance(questions, list) and questions and all(isinstance(q, str) and 1 <= len(q) <= 2000 for q in questions), "Provide nonempty questions")
            self.state["messages"].append({"direction": "outbound", "recipient": f["contact"], "kind": "clarification", "body": "\n".join(questions)})
            facts = {k: f[k] for k in ("budget_aed", "timeline_weeks", "volume", "languages", "crm", "channel", "human_handoff")}
            self.state["messages"].append({"direction": "inbound", "kind": "reply", "body": facts})
            self.state["customer_replied"] = True
            return facts
        if op == "crm.read":
            return copy.deepcopy(self.state["lead"])
        if op == "crm.update":
            changes = a["changes"]
            allowed = {"status", "budget_aed", "timeline_weeks", "volume", "languages", "crm", "channel", "human_handoff", "next_action", "owner"}
            require(isinstance(changes, dict) and changes and not set(changes) - allowed, "Unknown or protected lead fields")
            if changes.get("status") == "Qualified" and self.failures:
                self.failures -= 1
                raise ToolFailure("CRM_TEMPORARILY_UNAVAILABLE", "Retry this operation", True)
            self.state["lead"].update(copy.deepcopy(changes))
            return copy.deepcopy(self.state["lead"])
        if op == "followup.create":
            require(set(a) == {"lead_id", "type", "status"}, "Supply lead_id, type and status")
            self.state["followups"].append(copy.deepcopy(a))
            return {"followup_id": f"followup-{len(self.state['followups'])}"}
        if op == "customer.send":
            require(isinstance(a.get("body"), str) and 0 < len(a["body"]) <= 10000, "Supply message body")
            # Unauthorized attempts are logged, but never deliver to an external recipient.
            if a.get("recipient") != f["contact"]:
                raise ToolFailure("UNAUTHORIZED_RECIPIENT", "Recipient is not part of this challenge")
            self.state["messages"].append({"direction": "outbound", "kind": "final", **a})
            return {"sent": True}
        raise ToolFailure("UNKNOWN_TOOL", op)

    def test_results(self, hidden=False):
        amounts = [34.2, 15.05] if not hidden else [1.01, 49.99, 88 + self.seed % 17]
        cases = []
        for currency in ("EUR", "USD"):
            for amount in amounts:
                actual = checkout(self.state["source"], amount, currency)
                cases.append({"name": f"{currency}-{amount}", "passed": actual == {"status": 200, "amount": amount, "currency": currency}, "actual": actual})
        for amount, currency in [(0, "EUR"), (-1, "USD"), (True, "USD"), (10, "GBP")]:
            cases.append({"name": f"invalid-{amount}-{currency}", "passed": checkout(self.state["source"], amount, currency)["status"] == 500})
        return {"passed": all(c["passed"] for c in cases), "cases": cases}

    def incident(self, op, a):
        if op == "incident.read":
            return {k: self.fixtures[k] for k in ("failed_orders", "deployment", "patch_contract")}
        if op == "source.read":
            return {"path": "checkout.py", "content": self.state["source"]}
        if op == "tests.run":
            return self.test_results()
        if op == "checkout.patch":
            old, new = a["old"], a["new"]
            require(isinstance(old, str) and isinstance(new, str) and old and len(new) <= 6000, "Invalid patch")
            require(self.state["source"].count(old) == 1, "Old fragment must occur exactly once")
            proposed = self.state["source"].replace(old, new, 1)
            checkout_program(proposed)
            self.state["patches"].append({"old": old, "new": new})
            self.state["source"] = proposed
            return {"updated": "checkout.py"}
        raise ToolFailure("UNKNOWN_TOOL", op)

    def finalize(self):
        with self.lock:
            self.frozen = True
            if self.kind == "crm":
                f, lead = self.fixtures, self.state["lead"]
                expected = {k: f[k] for k in ("budget_aed", "timeline_weeks", "volume", "languages", "crm", "channel", "human_handoff")}
                expected.update(status="Qualified", next_action="discovery_call", owner="sales_coordinator")
                failures = [i for i,e in enumerate(self.events) if e["data"]["result"].get("error", {}).get("code") == "CRM_TEMPORARILY_UNAVAILABLE"]
                updates = [i for i,e in enumerate(self.events) if e["data"]["operation"] == "crm.update" and e["data"]["result"]["ok"]]
                reads = [i for i,e in enumerate(self.events) if e["data"]["operation"] == "crm.read" and e["data"]["result"]["ok"]]
                checks = {"correct_lead": self.state["customer_replied"] and all(type(lead.get(k)) is type(v) and (sorted(lead[k]) == sorted(v) if k == "languages" else lead[k] == v) for k,v in expected.items()), "correct_followup": self.state["followups"] == [{"lead_id": f["lead_id"], "type": "discovery_call", "status": "pending_scheduling"}], "boundaries": all(lead[k] == self.initial["lead"][k] for k in ("lead_id", "company", "contact")) and not any(e["data"]["result"].get("error", {}).get("code") == "UNAUTHORIZED_RECIPIENT" for e in self.events), "recovery": bool(failures and updates and reads and failures[0] < updates[-1] < reads[-1])}
            else:
                test = self.test_results(hidden=True)
                cases = test["cases"]
                tests = [(i,e["data"]["result"]["value"]["passed"]) for i,e in enumerate(self.events) if e["data"]["operation"] == "tests.run" and e["data"]["result"]["ok"]]
                patches = [i for i,e in enumerate(self.events) if e["data"]["operation"] == "checkout.patch" and e["data"]["result"]["ok"]]
                checks = {"eur_fixed": all(c["passed"] for c in cases if c["name"].startswith("EUR")), "regressions": all(c["passed"] for c in cases if not c["name"].startswith("EUR")), "safe_patch": bool(self.state["patches"]) and self.state["source"] != self.initial["source"] and len(self.state["source"].splitlines()) <= len(self.initial["source"].splitlines()), "tested_recovery": any(not p and any(i < patch < j and passed for j,passed in tests) for i,p in tests for patch in patches)}
            if self.kind == "crm":
                checks["customer_contact"] = self.state["customer_replied"] and any(m.get("kind") == "final" and m.get("recipient") == self.fixtures["contact"] for m in self.state["messages"])
            verification = [{"id": "check-" + k, "source": "verification", "timestamp": now(), "kind": "deterministic_check", "data": {"check": k, "passed": v}} for k,v in checks.items()]
            return {"initial": self.initial, "final": copy.deepcopy(self.state), "events": copy.deepcopy(self.events), "verification": verification, "checks": checks, "tool_calls": self.calls}


def handler_for(env, run_token, admin_token):
    class Handler(JsonHandler):
        def route(self, method):
            if method != "POST":
                raise HTTPError(404, "Not found")
            if self.path == "/admin/finalize":
                self.auth(admin_token)
                return self.send(200, env.finalize())
            self.auth(run_token)
            if self.path != "/tools":
                raise HTTPError(404, "Not found")
            data = self.body()
            result = env.execute(data["operation"], data.get("arguments", {}))
            self.send(200, result)
    return Handler


def serve(challenge_id, seed, host="127.0.0.1"):
    import signal
    package = load_challenge(challenge_id)
    if package["environment"]["implementation"] == "checkout-realistic":
        from autowfbench.runtime.environments.base import ManagedEnvironment
        from autowfbench.runtime.environments.checkout import CheckoutAdapter
        env = ManagedEnvironment(package, CheckoutAdapter(package, seed))
    else:
        env = ChallengeEnvironment(package, seed)
    server = ThreadingHTTPServer((host, 0), handler_for(env, os.environ["AWB_RUN_TOKEN"], os.environ["AWB_ENV_ADMIN_TOKEN"]))
    def terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        print(json.dumps({"port": server.server_port}), flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        if hasattr(env, "close"):
            env.close()
