from __future__ import annotations

from functools import lru_cache

import fastjsonschema

from autowfbench.core.common import ROOT, digest, read_json


@lru_cache(maxsize=16)
def validator(name):
    path = ROOT / "benchmark/schemas" / (name + ".schema.json")
    return fastjsonschema.compile({**read_json(path), "$id": path.as_uri()}, use_default=False)


def validate(name, value):
    validator(name)(value)
    return value


def load_challenge(challenge_id):
    if challenge_id not in {p.name for p in (ROOT / "benchmark/challenges").iterdir() if p.is_dir()}:
        raise ValueError("Unknown challenge")
    root = ROOT / "benchmark/challenges" / challenge_id
    package = {key: read_json(root / (key + ".json")) for key in ("definition", "environment", "scorecard")}
    package["environment"]["implementation_digest"] = digest((ROOT / "autowfbench/runtime/environment.py").read_text())
    if package["environment"]["implementation"] == "checkout-realistic":
        # Bind packaged assets and protected verifier code into the existing
        # environment hash, without changing the public run-log schema.
        paths = list((root / "assets").rglob("*"))
        for directory in ("environments", "verifiers"):
            paths.extend((ROOT / "autowfbench/runtime" / directory).glob("*.py"))
        package["environment"]["asset_digests"] = {
            str(path.relative_to(ROOT)): digest(path.read_text())
            for path in sorted(paths) if path.is_file() and "__pycache__" not in path.parts
        }
    validate("scorecard", package["scorecard"])
    definition = package["definition"]
    if definition["id"] != challenge_id:
        raise ValueError("Challenge ID mismatch")
    if sum(c["weight"] for c in package["scorecard"]["criteria"]) != 10:
        raise ValueError("Example challenge weights must sum to 10")
    ids = [c["id"] for c in package["scorecard"]["criteria"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate criterion IDs")
    package["hashes"] = {k: digest(v) for k, v in package.items()}
    return package


def public_challenges():
    return [load_challenge(p.name)["definition"] for p in sorted((ROOT / "benchmark/challenges").iterdir()) if p.is_dir()]
