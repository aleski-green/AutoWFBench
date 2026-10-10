"""Public simApp contracts; no grading rules or scenario fixtures."""
import copy


def obj(properties=None, required=None):
    properties = properties or {}
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
NUMBER = {"type": "number", "minimum": 0}
FACTS = {
    "budget_aed": {"type": ["number", "null"], "minimum": 0},
    "timeline_weeks": {"type": ["integer", "null"], "minimum": 1},
    "volume": {"type": ["integer", "null"], "minimum": 0},
    "languages": {"type": "array", "items": TEXT, "minItems": 1},
    "crm": TEXT, "channel": TEXT, "human_handoff": {"type": "boolean"},
}
ROUTES = {
    "qualified": {"status": "Qualified", "next_action": "discovery_call", "owner": "sales_coordinator"},
    "not_qualified": {"status": "NotQualified", "next_action": "close_lost", "owner": "sales_coordinator"},
    "needs_review": {"status": "NeedsReview", "next_action": "clarify_requirements", "owner": "sales_coordinator"},
}
POLICY = {
    "qualification": {"minimum_budget_aed": 100000, "maximum_timeline_weeks": 16},
    "supported": {"channel": ["WhatsApp"], "languages": ["Arabic", "English"], "human_handoff": True},
    "subject_to_assessment": ["Salesforce", "HubSpot"],
    "routes": ROUTES,
    "decision": "Missing/null facts => needs_review. Otherwise budget below minimum, timeline above maximum, unsupported channel/language or CRM => not_qualified. Otherwise qualified; CRM integration still needs assessment.",
    "followup": {"qualified": "discovery_call", "not_qualified": "close_lost", "needs_review": "clarify_requirements", "status": "pending"},
    "communication": "Do not guarantee price, deployment, accuracy or compliance. State the actual route, open questions and next step. On permanent write failure, do not create a follow-up; report the block and contact the customer honestly.",
}
CHANGE_FIELDS = {**FACTS, **{k: {"enum": sorted({v[k] for v in ROUTES.values()})} for k in ("status", "next_action", "owner")}}
LEAD = obj({"lead_id": TEXT, "company": TEXT, "contact": TEXT, "status": TEXT, **{k:v for k,v in CHANGE_FIELDS.items() if k != "status"}}, ["lead_id", "company", "contact", "status"])


def shape(value):
    """Expose response field names/types without pinning runtime business values."""
    if isinstance(value, dict):
        return obj({k:shape(v) for k,v in value.items()})
    if isinstance(value, list):
        return {"type":"array", "items":shape(value[0])}
    return {"type": "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int,float)) else "string"}


def operation(app, name, doc, arguments, result):
    return {"app": app, "operation": name, "method": "POST", "path": "/apps/" + app + "/" + name, "doc": doc, "arguments": arguments, "result": result}


CRM = [
    operation("messaging", "inquiry.read", "Read the inquiry and authorized contact.", obj(), obj({"lead_id": TEXT, "contact": TEXT, "body": TEXT})),
    operation("policy", "documents.read", "Read qualification rules, routes and communication constraints.", obj(), shape(POLICY)),
    operation("research", "research.read", "Read company evidence and contact authority.", obj(), obj({"company": TEXT, "industry": TEXT, "contact_authority": TEXT, "sources": {"type": "array", "items": TEXT}})),
    operation("messaging", "customer.ask", "Ask for all seven qualification fields in one clear message. Null means unknown; never invent it.", obj({"questions": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 7}}), obj(FACTS)),
    operation("crm", "crm.read", "Read the current lead; compare fields after any write before dependent actions.", obj(), LEAD),
    operation("crm", "crm.update", "Update only provided fields. Use one idempotency key per intended change. Retry only retryable errors, at most three attempts. An ambiguous response may have committed; reuse the key and read back. Permanent errors leave state unchanged.", obj({"changes": {**obj(CHANGE_FIELDS, []), "minProperties": 1}, "idempotency_key": TEXT}), LEAD),
    operation("crm", "followup.create", "After verified lead update create exactly one route-appropriate follow-up. Reusing the same key never duplicates it.", obj({"lead_id": TEXT, "type": {"enum": ["discovery_call", "close_lost", "clarify_requirements"]}, "status": {"const": "pending"}, "idempotency_key": TEXT}), obj({"followup_id": TEXT})),
    operation("crm", "followup.read", "Read all follow-ups for this run to verify delivery and detect duplicates.", obj(), {"type": "array", "items": obj({"followup_id": TEXT, "lead_id": TEXT, "type": TEXT, "status": TEXT})}),
    operation("messaging", "customer.send", "Send one concise final message to the inquiry contact; provide an idempotency key.", obj({"recipient": TEXT, "body": TEXT, "idempotency_key": TEXT}), obj({"sent": {"type": "boolean"}, "message_id": TEXT})),
]
CHECKOUT = [
    operation("incidents", "incident.read", "Read failed orders, recent deployment and patch contract. Do not assume the affected currency.", obj(), obj({"failed_orders": {"type": "array", "items": obj({"order_id": TEXT, "amount": NUMBER, "currency": TEXT})}, "deployment": TEXT, "patch_contract": TEXT})),
    operation("repository", "source.read", "Read checkout.py from the run's isolated simulated repository.", obj(), obj({"path": TEXT, "content": TEXT})),
    operation("repository", "checkout.patch", "Replace one unique old source fragment with new. Keep changes minimal. Reuse the idempotency key for at most three retryable attempts; permanent errors require reporting a block. Ambiguous responses may have committed. Read back before claiming repair.", obj({"old": TEXT, "new": TEXT, "idempotency_key": TEXT}), obj({"updated": TEXT})),
    operation("tests", "tests.run", "Run public EUR/USD positive and invalid-input tests. Observe failure before editing and verify after. Payment validation must remain intact.", obj(), obj({"passed": {"type": "boolean"}, "cases": {"type": "array", "items": obj({"name":TEXT, "passed":{"type":"boolean"}, "actual":obj({"status":{"type":"integer"}, "amount":NUMBER, "currency":TEXT, "error":TEXT}, ["status"])}, ["name","passed"])}})),
]


def catalog(kind):
    return {"version": "2.0.0", "doc": "simApps use the run-scoped Bearer token. POST each path with the argument object. HTTP 200 wraps business results as {ok:true,value:<result schema>} or {ok:false,error:{code,message,retryable}}. Inspect ok. GET /apps returns this catalog. Read operations are side-effect free. Instances are reset for every run. Never access /admin. Runtime data, not example constants, must drive decisions.", "operations": copy.deepcopy(CRM if kind == "crm" else CHECKOUT)}
