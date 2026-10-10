import argparse
import json
import os
from pathlib import Path

from autowfbench.core.common import read_json


def main():
    parser = argparse.ArgumentParser(description="Generate, execute and evaluate workflows")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, port in (("judge", 9100), ("example-solution", 9201)):
        p = sub.add_parser(name)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=port)
        if name == "judge":
            p.add_argument("--model", default=os.environ.get("AWB_JUDGE_MODEL"))
            p.add_argument("--data-dir", type=Path, default=Path("runs/judge"))
        else:
            p.add_argument("--variant", choices=("reference", "incomplete"), default="reference")
    p = sub.add_parser("environment")
    p.add_argument("challenge_id")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--host", default="127.0.0.1")
    p = sub.add_parser("run")
    p.add_argument("challenge_id")
    p.add_argument("solution", type=Path)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--data-dir", type=Path, default=Path("runs"))
    p.add_argument("--judge-url", default=os.environ.get("AWB_JUDGE_URL"))
    p = sub.add_parser("validate-submission")
    p.add_argument("file", type=Path)
    p = sub.add_parser("compose")
    p.add_argument("challenge_id", choices=("all", "crm-lead-qualification", "production-checkout-recovery"))
    p.add_argument("--data-dir", type=Path, default=Path("runs/composer"))
    p.add_argument("--attempts", type=int, choices=range(1, 6), default=3)
    p.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    p = sub.add_parser("evaluate")
    p.add_argument("challenge_id")
    p.add_argument("workflow", type=Path)
    p.add_argument("--seeds", type=int, nargs="+", default=list(range(6, 12)))
    p.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    p.add_argument("--data-dir", type=Path, default=Path("runs/evaluation"))
    args = parser.parse_args()
    if args.command in ("compose", "evaluate"):
        from autowfbench.interfaces.composer.search import compose, evaluate, public_input
        from autowfbench.interfaces.composer.n8n import N8nRuntime, validate_workflow
        challenges = ("crm-lead-qualification", "production-checkout-recovery") if args.challenge_id == "all" else (args.challenge_id,)
        for challenge in challenges:
            directory = args.data_dir.resolve() / challenge
            if args.command == "compose":
                workflow = compose(challenge, directory, args.attempts)
                seeds = list(range(6, 12))
            else:
                workflow = validate_workflow(read_json(args.workflow), public_input(challenge)["apps"])
                seeds = args.seeds
            summary, _ = evaluate(workflow, challenge, seeds, directory / "held-out", N8nRuntime(), repeats=args.repeats)
            print(json.dumps({"challenge":challenge,"score":summary["score"],"mean":summary["mean"],"summary":str(directory / "held-out/summary.json")}))
        return
    if args.command == "environment":
        from autowfbench.runtime.environment.server import serve
        return serve(args.challenge_id, args.seed, args.host)
    if args.command == "judge":
        from autowfbench.runtime.judge import serve
        return serve(args.host, args.port, "codex", args.model, args.data_dir)
    if args.command == "example-solution":
        from autowfbench.interfaces.solution import serve
        return serve(args.host, args.port, args.variant)
    if args.command == "validate-submission":
        from autowfbench.core.contracts import validate
        validate("submission", read_json(args.file))
        print("Submission valid")
        return
    from autowfbench.runtime.engine import Engine
    engine = Engine(args.data_dir, args.judge_url, os.environ.get("AWB_JUDGE_TOKEN"))
    run_id = engine.submit(args.challenge_id, read_json(args.solution), args.seed, background=False)
    result = engine.read(run_id)
    print(json.dumps(result, indent=2))
    if result["status"] in ("engine_error", "judge_failed"):
        raise SystemExit(1)
