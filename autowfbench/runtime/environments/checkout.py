"""Real checkout application with a fresh workspace and a small tool surface."""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
import shutil
import tempfile

from autowfbench.core.common import ROOT
from .base import OperationError
from .sandbox import DockerSandbox


SOURCE_PATHS = {"app.py", "orders.py", "payments.py", "tests/test_checkout.py"}
EVIDENCE_PATHS = {"logs/production.log", "data/failed_orders.json", "deployment/latest.diff"}
PUBLIC_PATHS = SOURCE_PATHS | EVIDENCE_PATHS


def validate_patch(original, proposed):
    """Keep module-level code and the checkout interface immutable."""
    try:
        before, after = ast.parse(original), ast.parse(proposed)
    except SyntaxError as exc:
        raise OperationError("INVALID_PATCH", "Patch must be valid Python") from exc
    def skeleton(tree):
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "checkout"]
        if len(functions) != 1:
            raise OperationError("INVALID_PATCH", "Keep one checkout function")
        functions[0].body = [ast.Pass()]
        return ast.dump(tree, include_attributes=False)
    if skeleton(before) != skeleton(after):
        raise OperationError("INVALID_PATCH", "Only the checkout function body may change")


class CheckoutAdapter:
    def __init__(self, package, seed=0, sandbox_factory=DockerSandbox):
        self.package, self.seed = package, seed
        self._temp = tempfile.TemporaryDirectory(prefix="awb-checkout-")
        self.workspace = Path(self._temp.name).resolve() / "app"
        self.patches = []
        self.closed = False
        try:
            assets = ROOT / "benchmark/challenges" / package["definition"]["id"] / "assets"
            self.workspace.mkdir(mode=0o755)
            for name in sorted(PUBLIC_PATHS):
                source = assets / name
                if source.is_symlink() or not source.is_file():
                    raise ValueError("Missing or unsafe packaged checkout asset: " + name)
                destination = self.workspace / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                destination.chmod(0o644)
            runtime = package["environment"]["runtime"]
            self.sandbox = sandbox_factory(self.workspace, runtime["image"], runtime["timeout_seconds"])
        except Exception:
            self._temp.cleanup()
            raise

    def _path(self, name):
        path = self.workspace / name
        if path.is_symlink() or path.resolve().parent not in {self.workspace, self.workspace / "tests", self.workspace / "logs", self.workspace / "data", self.workspace / "deployment"}:
            raise OperationError("FORBIDDEN_PATH", "Path is not accessible")
        return path

    def snapshot(self):
        files = {name: self._path(name).read_text() for name in sorted(PUBLIC_PATHS)}
        summary = self.workspace / "incident_summary.md"
        if summary.exists():
            files["incident_summary.md"] = self._path("incident_summary.md").read_text()
        return {"files": files, "hashes": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}, "patches": copy.deepcopy(self.patches)}

    def execute(self, operation, arguments):
        if operation == "capabilities.list":
            return {"capabilities": copy.deepcopy(self.package["environment"]["capability_contracts"])}
        if operation in ("source.read", "evidence.read"):
            name = arguments["path"]
            allowed = SOURCE_PATHS if operation == "source.read" else EVIDENCE_PATHS
            if name not in allowed:
                raise OperationError("FORBIDDEN_PATH", "Path is not accessible")
            return {"path": name, "content": self._path(name).read_text()}
        if operation == "checkout.patch":
            path = self._path("app.py")
            source = path.read_text()
            old, new = arguments["old"], arguments["new"]
            if not old or old == new or source.count(old) != 1:
                raise OperationError("INVALID_PATCH", "Old fragment must be unique and replacement must change it")
            proposed = source.replace(old, new, 1)
            if len(proposed) > 20000:
                raise OperationError("INVALID_PATCH", "Source size limit exceeded")
            validate_patch(source, proposed)
            temporary = self.workspace / ".app.py.new"
            temporary.write_text(proposed)
            temporary.chmod(0o644)
            temporary.replace(path)
            record = {"old": old, "new": new, "before_sha256": hashlib.sha256(source.encode()).hexdigest(), "after_sha256": hashlib.sha256(proposed.encode()).hexdigest()}
            self.patches.append(record)
            return {"updated": "app.py", **record}
        if operation == "artifact.write":
            self._path("incident_summary.md").write_text(arguments["content"])
            return {"written": "incident_summary.md"}
        if operation in ("tests.run", "checkout.observe"):
            request = {"mode": "tests"} if operation == "tests.run" else {"mode": "observe", "order": arguments["order"]}
            result = self.sandbox.run(request)
            if "execution_error" in result:
                raise OperationError("EXECUTION_FAILED", "Application raised " + str(result["execution_error"]))
            if operation == "tests.run" and (type(result.get("passed")) is not bool or type(result.get("tests_run")) is not int or result["tests_run"] < 1):
                raise OperationError("EXECUTION_FAILED", "Public tests returned an invalid result")
            if operation == "checkout.observe" and "response" not in result:
                raise OperationError("EXECUTION_FAILED", "Checkout returned an invalid result")
            return result
        raise OperationError("UNKNOWN_TOOL", "Operation is not available")

    def verify(self, initial, events):
        from autowfbench.runtime.verifiers.checkout import verify
        return verify(self, initial, events)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.sandbox.close()
            finally:
                self._temp.cleanup()
