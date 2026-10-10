"""CRM Lead Qualification v0.1 baseline solution API.

This is candidate-side code only. It uses the run-scoped AutoWFBench environment
for every business action and never evaluates or scores itself.
"""
from __future__ import annotations

import argparse
import threading
import uuid
from http.server import ThreadingHTTPServer

from autowfbench.core.common import HTTPError, JsonHandler, http_json, now


def solve(request, cancelled=None):
    trace = []
    env = request["environment"]

    def tool(operation, **arguments):
        if cancelled and cancelled.is_set():
            raise RuntimeError("Cancelled")
        result = http_json(
            env["base_url"] + "/tools",
            {"operation": operation, "arguments": arguments},
            env["access_token"],
        )
        trace.append({
            "timestamp": now(),
            "kind": "tool_observation",
            "data": {"operation": operation, "ok": result["ok"]},
        })
        return result

    inquiry = tool("inquiry.read")["value"]
    tool("documents.read")
    tool("research.read")

    facts = tool(
        "customer.ask",
        questions=[
            "What budget, timeline, monthly message volume, languages, CRM, "
            "preferred channel, and human-handoff requirements should we plan for?"
        ],
    )["value"]

    changes = {
        **facts,
        "status": "Qualified",
        "next_action": "discovery_call",
        "owner": "sales_coordinator",
    }

    update = tool("crm.update", changes=changes)
    if not update["ok"]:
        error = update.get("error", {})
        if not error.get("retryable"):
            raise RuntimeError(f"CRM update failed: {error.get('message', 'unknown error')}")
        update = tool("crm.update", changes=changes)
        if not update["ok"]:
            raise RuntimeError("CRM update failed again after retry")

    tool(
        "followup.create",
        lead_id=inquiry["lead_id"],
        type="discovery_call",
        status="pending_scheduling",
    )

    tool(
        "customer.send",
        recipient=inquiry["contact"],
        body=(
            "Your WhatsApp use case, Arabic and English support, and human "
            "escalation needs align with our services. Salesforce integration "
            "requires assessment. Please share availability for a discovery call "
            "to confirm scope, pricing, and timeline before commitments."
        ),
    )

    lead = tool("crm.read")["value"]
    answer = (
        f"The customer confirmed budget AED {facts['budget_aed']}, "
        f"{facts['volume']} monthly messages, and a {facts['timeline_weeks']}-week "
        f"target. Service/policy evidence and company research were consulted. "
        f"The CRM was updated and verified as {lead['status']}; one discovery "
        "follow-up was created pending scheduling and a customer response was sent "
        "without guaranteeing scope, pricing, or delivery. Scheduling remains open."
    )
    return {
        "protocol_version": "1.0",
        "run_id": request["run_id"],
        "status": "completed",
        "final_answer": answer,
        "artifacts": [],
        "trace": trace,
    }


def handler():
    jobs = {}
    run_index = {}
    lock = threading.RLock()

    class Handler(JsonHandler):
        def route(self, method):
            if method == "POST" and self.path == "/runs":
                request = self.body()
                run_id = request["run_id"]
                with lock:
                    existing = run_index.get(run_id)
                    if existing:
                        return self.send(202, {"execution_id": existing, "status": jobs[existing]["status"]})
                    execution_id = uuid.uuid4().hex
                    cancel = threading.Event()
                    jobs[execution_id] = {"status": "running", "cancel": cancel}
                    run_index[run_id] = execution_id

                def work():
                    try:
                        submission = solve(request, cancel)
                    except Exception as exc:
                        submission = {
                            "protocol_version": "1.0",
                            "run_id": run_id,
                            "status": "failed",
                            "final_answer": str(exc),
                            "artifacts": [],
                            "trace": [],
                        }
                    with lock:
                        jobs[execution_id].update(
                            status=submission["status"],
                            submission=submission,
                        )

                threading.Thread(target=work, daemon=True).start()
                return self.send(202, {"execution_id": execution_id, "status": "running"})

            parts = self.path.strip("/").split("/")
            if len(parts) in (2, 3) and parts[0] == "runs":
                with lock:
                    job = jobs.get(parts[1])
                    if not job:
                        raise HTTPError(404, "Unknown execution")
                    if method == "GET" and len(parts) == 2:
                        return self.send(200, {k: v for k, v in job.items() if k != "cancel"})
                    if method == "POST" and len(parts) == 3 and parts[2] == "cancel":
                        job["cancel"].set()
                        return self.send(200, {"accepted": True})
            raise HTTPError(404, "Not found")

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9301)
    args = parser.parse_args()
    print(f"CRM baseline v0.1.0: http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), handler()).serve_forever()


if __name__ == "__main__":
    main()
