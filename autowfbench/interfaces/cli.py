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
    args = parser.parse_args()
    if args.command == "environment":
        from autowfbench.runtime.environment import serve
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
