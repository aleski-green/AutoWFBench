"""Scores come from validated evidence and fixed weights, never a supplied total."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from autowfbench.core.common import digest
from autowfbench.core.contracts import validate

ANSWER_VALUES = {"yes": Decimal("1"), "maybe": Decimal("0.33"), "no": Decimal("0")}


def validate_judgement(run, card, response):
    validate("judge-response", response)
    if response["run_id"] != run["run_id"] or response["scorecard_digest"] != digest(card):
        raise ValueError("Judge response does not match run and scorecard")
    expected = {c["id"] for c in card["criteria"] if c["evaluator"] == "llm"}
    ids = [c["criterion_id"] for c in response["criteria"]]
    if len(ids) != len(set(ids)) or not set(ids) <= expected:
        raise ValueError("Duplicate or unknown judge criteria")
    if response["status"] == "complete" and set(ids) != expected:
        raise ValueError("Missing judge criteria")
    evidence = {e["id"]: e["source"] for e in run["events"]}
    requirements = {c["id"]: set(c["required_evidence"]) for c in card["criteria"]}
    for criterion in response["criteria"]:
        if not set(criterion["evidence_refs"]) <= set(evidence):
            raise ValueError("Judge cited nonexistent evidence")
        if not requirements[criterion["criterion_id"]] <= {evidence[ref] for ref in criterion["evidence_refs"]}:
            raise ValueError("Judge omitted a required evidence source")
    return response


def calculate(run, card, judgement=None, provenance=None, error=None):
    answers = {}
    if judgement is not None:
        validate_judgement(run, card, judgement)
        if judgement["status"] == "complete":
            answers = {c["criterion_id"]: c for c in judgement["criteria"]}
    rows = []
    total, deterministic = Decimal(0), Decimal(0)
    for c in card["criteria"]:
        row = {"id": c["id"], "question": c["question"], "weight": c["weight"], "evaluator": c["evaluator"], "answer": None, "points": None, "reason": "Awaiting LLM judge", "evidence_refs": []}
        if c["evaluator"] == "deterministic":
            if c["check"] not in run["checks"] or type(run["checks"][c["check"]]) is not bool:
                raise ValueError("Missing or non-boolean verification result")
            row.update(answer="yes" if run["checks"][c["check"]] else "no", reason="Protected environment verification", evidence_refs=["check-" + c["check"]])
        elif c["id"] in answers:
            row.update({k: answers[c["id"]][k] for k in ("answer", "reason", "evidence_refs")})
        if row["answer"]:
            points = Decimal(str(c["weight"])) * ANSWER_VALUES[row["answer"]]
            row["points"] = float(points)
            total += points
            if c["evaluator"] == "deterministic":
                deterministic += points
        rows.append(row)
    complete = all(r["answer"] is not None for r in rows)
    score = total * Decimal(10) / sum(Decimal(str(c["weight"])) for c in card["criteria"])
    for row in rows:
        if row["answer"] == "no" and row["id"] in card.get("gates", {}):
            score = min(score, Decimal(str(card["gates"][row["id"]])))
    execution_checks = [c["check"] for c in card["criteria"] if c["evaluator"] == "deterministic" and c["id"] in card.get("gates", {})] or list(run["checks"])
    return {"run_id": run["run_id"], "status": "complete" if complete else ("judge_failed" if error else "awaiting_llm_judge"), "score_0_10": float(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if complete else None, "deterministic_points": float(deterministic), "execution_pass": all(run["checks"][name] for name in execution_checks) and run["termination_reason"] == "completed", "judge": provenance, "judge_error": error, "criteria": rows, "answer_values": {k: float(v) for k,v in ANSWER_VALUES.items()}}
