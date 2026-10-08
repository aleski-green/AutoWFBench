"""Scripted environment smoke demonstration; not a Composer/n8n benchmark."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import threading
import uuid

from autowfbench.core.common import JsonHandler, background_server, http_json, now, read_json, save_json
from autowfbench.runtime.engine import Engine


CHALLENGE = "production-checkout-recovery-realistic"


def demonstrate(request):
    environment = request["environment"]
    trace = []

    def call(operation, arguments=None):
        result = http_json(environment["base_url"] + "/tools", {"operation": operation, "arguments": arguments or {}}, environment["access_token"])
        trace.append({"timestamp": now(), "kind": "demonstration_tool_result", "data": {"operation": operation, "arguments": arguments or {}, "result": result}})
        return result

    call("capabilities.list")
    for path in ("logs/production.log", "data/failed_orders.json", "deployment/latest.diff"):
        call("evidence.read", {"path": path})
    source = call("source.read", {"path": "app.py"})["value"]["content"]
    call("source.read", {"path": "payments.py"})
    before = call("tests.run")
    # A scripted, explicitly labeled smoke repair derived from the read source.
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "charge_card"]
    call_node = next(n for n in calls if isinstance(n.args[0], ast.Subscript) and isinstance(n.args[0].slice, ast.Constant) and n.args[0].slice.value == "currency")
    old = ast.get_source_segment(source, call_node)
    replacement = "charge_card(" + ", ".join(ast.get_source_segment(source, arg) for arg in reversed(call_node.args)) + ")"
    call("checkout.patch", {"old": old, "new": replacement})
    call("source.read", {"path": "app.py"})
    after = call("tests.run")
    observation = call("checkout.observe", {"order": {"order_id": "demo-eur", "customer_id": "demo-customer", "currency": "EUR", "items": [{"unit_price": "11.40", "quantity": 3}]}})
    recovered = (before.get("ok") is True and before["value"].get("passed") is False
                 and after.get("ok") is True and after["value"].get("passed") is True
                 and observation.get("ok") is True and observation["value"].get("response", {}).get("status") == 200)
    verification = "Observed failing public tests before the patch, passing public tests afterward, and a successful EUR probe." if recovered else "Runtime recovery is NOT established. Inspect the recorded tool errors/results; no successful application execution is claimed."
    summary = ("# Root cause\nThe EUR branch passes currency where charge_card expects amount.\n\n"
               "# Customer impact\nThe incident evidence records EUR checkout failures after deployment, while USD requests continued succeeding.\n\n"
               "# Fix\nThe scripted smoke demonstration corrected argument order in app.py through checkout.patch.\n\n"
               "# Verification\n" + verification + "\n")
    artifact = call("artifact.write", {"content": summary})
    return {"protocol_version": "1.0", "run_id": request["run_id"], "status": "completed" if recovered and artifact["ok"] else "failed",
            "final_answer": verification, "artifacts": [{"name": "incident_summary.md", "media_type": "text/markdown", "content": summary}], "trace": trace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("runs/realistic-environment-demo"))
    args = parser.parse_args()
    jobs, lock = {}, threading.Lock()

    class Handler(JsonHandler):
        def route(self, method):
            if method == "POST" and self.path == "/runs":
                request = self.body()
                execution_id = uuid.uuid4().hex
                with lock:
                    jobs[execution_id] = {"status": "running"}
                def work():
                    try:
                        submission = demonstrate(request)
                    except Exception as exc:
                        submission = {"protocol_version": "1.0", "run_id": request["run_id"], "status": "failed", "final_answer": "Demo failed: " + str(exc), "artifacts": [], "trace": []}
                    with lock:
                        jobs[execution_id] = {"status": submission["status"], "submission": submission}
                threading.Thread(target=work, daemon=True).start()
                return self.send(202, {"execution_id": execution_id})
            with lock:
                result = jobs.get(self.path.rsplit("/", 1)[-1])
            return self.send(200 if result else 404, result or {"error": "Not found"})

    server = background_server(Handler)
    try:
        engine = Engine(args.data_dir)
        manifest = {"id": "realistic-environment-smoke", "name": "Scripted environment demonstration", "version": "1.0.0", "runtime": "scripted-reference", "description": "Exercises the existing tool interface; does not use Composer or n8n", "auth_env": "", "endpoint": f"http://127.0.0.1:{server.server_port}"}
        run_id = engine.submit(CHALLENGE, manifest, background=False)
        result = engine.read(run_id)
        run = read_json(engine.directory(run_id) / "run-log.json")
        report = {"run_id": run_id, "task": read_json(engine.directory(run_id) / "package.json")["definition"]["task"],
                  "result": result, "checks": run["checks"], "operations": [e["data"] for e in run["events"] if e["source"] == "environment"],
                  "verification": [e["data"] for e in run["events"] if e["id"] == "verification-details"],
                  "run_directory": str(engine.directory(run_id)), "scope": "Environment-only scripted demonstration; no Composer or n8n execution"}
        save_json(args.data_dir / "demonstration.json", report)
        print(json.dumps({"run_id": run_id, "result": result, "checks": run["checks"], "report": str(args.data_dir / "demonstration.json")}, indent=2))
        return 0 if result.get("execution_pass") else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
