"""Scripted HTTP adapters demonstrating the submission protocol, not AI results."""
from __future__ import annotations

import threading
import uuid
from http.server import ThreadingHTTPServer

from autowfbench.core.common import HTTPError, JsonHandler, http_json, now


def solve(request, variant="reference", cancelled=None):
    trace = []
    env = request["environment"]
    def tool(operation, **arguments):
        if cancelled and cancelled.is_set():
            raise RuntimeError("Cancelled")
        result = http_json(env["base_url"] + "/tools", {"operation": operation, "arguments": arguments}, env["access_token"])
        trace.append({"timestamp": now(), "kind": "tool_observation", "data": {"operation": operation, "ok": result["ok"]}})
        return result
    def write(operation, **arguments):
        arguments["idempotency_key"] = request["run_id"] + operation
        for _ in range(3):
            result = tool(operation, **arguments)
            if result["ok"] or not result["error"]["retryable"] or variant == "incomplete":
                return result
        return result
    artifacts = []
    if request["challenge"]["id"] == "crm-lead-qualification":
        inquiry = tool("inquiry.read")["value"]
        policy = tool("documents.read")["value"]
        tool("research.read")
        facts = tool("customer.ask", questions=["Please confirm budget in AED, timeline in weeks, monthly volume, languages, CRM, channel and human handoff requirements."])["value"]
        route = "needs_review" if any(v is None for v in facts.values()) else "not_qualified" if facts["budget_aed"] < policy["qualification"]["minimum_budget_aed"] or facts["timeline_weeks"] > policy["qualification"]["maximum_timeline_weeks"] else "qualified"
        changes = {**facts, **policy["routes"][route]}
        update = write("crm.update", changes=changes)
        lead = tool("crm.read")["value"]
        confirmed = all(lead.get(k) == v for k,v in changes.items())
        if variant != "incomplete":
            if confirmed:
                write("followup.create", lead_id=inquiry["lead_id"], type=policy["followup"][route], status=policy["followup"]["status"])
                tool("followup.read")
            body = f"Assessment: {route}. " + ("Recorded in CRM; the next step is " + changes["next_action"] if confirmed else "CRM write is blocked; staff must restore access before recording the assessment or creating a follow-up.") + ". Integration, scope, price and timing require assessment; no commitments are made."
            write("customer.send", recipient=inquiry["contact"], body=body)
        answer = f"Customer facts: {facts}. Policy and company evidence consulted. Assessment: {route}. CRM readback: {lead}. Update result: {update}. " + ("Follow-up verified; scheduling or clarification remains open." if confirmed and variant != "incomplete" else "Work is incomplete; no follow-up claimed.")
    else:
        incident = tool("incident.read")["value"]
        source = tool("source.read")["value"]
        before = tool("tests.run")["value"]
        if variant != "incomplete":
            result = write("checkout.patch", old="charge_card(currency, amount)", new="charge_card(amount, currency)")
            readback = tool("source.read")["value"]
            after = tool("tests.run")["value"]
            answer = f"Incident: {incident}. Source passes currency before amount to charge_card. Changed that call only. Write result: {result}. Source readback: {readback}. Public tests before: {before}; after: {after}. " + ("Repair verified in the simulator; production deployment and monitoring remain open." if after["passed"] else "Write blocked and incident unresolved; restore write access, then retry and verify. No production deployment occurred.")
        else:
            answer = "Checkout still fails. No patch applied; repair remains open."
        artifacts = [{"name": "incident-summary.md", "media_type": "text/markdown", "content": answer}]
    return {"protocol_version": "1.0", "run_id": request["run_id"], "status": "completed", "final_answer": answer, "artifacts": artifacts, "trace": trace}


def handler_for(variant="reference", executor=None):
    jobs, lock = {}, threading.RLock()
    class Handler(JsonHandler):
        def route(self, method):
            if method == "POST" and self.path == "/runs":
                request = self.body()
                execution_id = uuid.uuid4().hex
                cancel = threading.Event()
                with lock:
                    jobs[execution_id] = {"status": "running", "cancel": cancel}
                def work():
                    try:
                        result = executor(request, cancel) if executor else solve(request, variant, cancel)
                    except Exception as exc:
                        result = {"protocol_version": "1.0", "run_id": request["run_id"], "status": "failed", "final_answer": str(exc), "artifacts": [], "trace": []}
                    with lock:
                        jobs[execution_id].update(status=result["status"], submission=result)
                threading.Thread(target=work, daemon=True).start()
                return self.send(202, {"execution_id": execution_id, "status": "running"})
            parts = self.path.strip("/").split("/")
            if len(parts) in (2,3) and parts[0] == "runs":
                with lock:
                    job = jobs.get(parts[1])
                    if not job:
                        raise HTTPError(404, "Unknown execution")
                    if method == "POST" and len(parts) == 3 and parts[2] == "cancel":
                        job["cancel"].set()
                        return self.send(200, {"accepted": True})
                    if method == "GET" and len(parts) == 2:
                        return self.send(200, {k:v for k,v in job.items() if k != "cancel"})
            raise HTTPError(404, "Not found")
    return Handler


def serve(host, port, variant):
    print(f"Scripted reference solution ({variant}): http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), handler_for(variant)).serve_forever()
