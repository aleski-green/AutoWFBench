"""Regression checks for the recursive layout and shared submission contract."""
import subprocess
import sys
import tempfile
import unittest

from autowfbench.core.common import ROOT, background_server, read_json
from autowfbench.core.contracts import validate


class LayoutTests(unittest.TestCase):
    def test_nested_submission_obeys_standalone_contract(self):
        log = read_json(ROOT / "benchmark/examples/run-log.json")
        valid = read_json(ROOT / "benchmark/examples/submission.json")
        validate("submission", valid)
        validate("run-log", {**log, "submission": valid})
        validate("run-log", {**log, "submission": None})
        mutations = [
            {**valid, "score": 10}, {**valid, "status": "invented"},
            {**valid, "trace": [{}]}, {**valid, "artifacts": [{}]},
            {**valid, "artifacts": [{"name": "x", "media_type": "text/plain", "content": "x" * 200001}]},
        ]
        mutations += [{k: v for k, v in valid.items() if k != required} for required in valid]
        for submission in mutations:
            with self.subTest(submission_keys=list(submission)):
                for name, value in (("submission", submission), ("run-log", {**log, "submission": submission})):
                    with self.assertRaises(ValueError):
                        validate(name, value)

    def test_local_schema_resolution_does_not_depend_on_working_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([
                sys.executable, "-c",
                "from autowfbench.core.contracts import validate; "
                "from autowfbench.core.common import ROOT, read_json; "
                "validate('run-log', read_json(ROOT / 'benchmark/examples/run-log.json'))",
            ], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
