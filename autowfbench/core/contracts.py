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
    package["environment"]["implementation_digest"] = digest({name: (ROOT / "autowfbench/runtime" / name).read_text() for name in ("environment.py", "apps.py")})
    validate("scorecard", package["scorecard"])
    definition = package["definition"]
    if definition["id"] != challenge_id:
        raise ValueError("Challenge ID mismatch")
    if sum(c["weight"] for c in package["scorecard"]["criteria"]) != 10:
        raise ValueError("Example challenge weights must sum to 10")
    ids = [c["id"] for c in package["scorecard"]["criteria"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate criterion IDs")
    if not set(package["scorecard"].get("gates", {})) <= set(ids):
        raise ValueError("Score gate references an unknown criterion")
    package["hashes"] = {k: digest(v) for k, v in package.items()}
    return package


def public_challenges():
    return [load_challenge(p.name)["definition"] for p in sorted((ROOT / "benchmark/challenges").iterdir()) if p.is_dir()]
