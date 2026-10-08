"""Run-scoped recording and lifecycle for managed application adapters."""
from __future__ import annotations

import copy
import threading
from typing import Protocol

import fastjsonschema

from autowfbench.core.common import now


class OperationError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class EnvironmentAdapter(Protocol):
    def snapshot(self) -> dict: ...
    def execute(self, operation: str, arguments: dict) -> dict: ...
    def verify(self, initial: dict, events: list) -> tuple[dict, dict]: ...
    def close(self) -> None: ...


class ManagedEnvironment:
    """Serialize mutations; freeze once; retain authoritative tool evidence."""

    def __init__(self, package, adapter: EnvironmentAdapter):
        self.package, self.adapter = package, adapter
        self.lock = threading.RLock()
        self.calls, self.frozen = 0, False
        self.events, self.evidence = [], None
        contracts = package["environment"]["capability_contracts"]
        self.validators = {c["name"]: fastjsonschema.compile(c["argument_schema"]) for c in contracts}
        if set(self.validators) != set(package["definition"]["capabilities"]):
            raise ValueError("Public capability names and contracts differ")
        self.initial = adapter.snapshot()

    def execute(self, operation, arguments):
        with self.lock:
            if self.frozen:
                return {"ok": False, "error": {"code": "RUN_CLOSED", "message": "Run is frozen", "retryable": False}}
            self.calls += 1
            try:
                if self.calls > self.package["definition"]["limits"]["tool_calls"]:
                    raise OperationError("TOOL_LIMIT", "Tool budget exhausted")
                if not isinstance(operation, str) or operation not in self.validators:
                    raise OperationError("UNKNOWN_TOOL", "Operation is not available")
                self.validators[operation](arguments)
                value = self.adapter.execute(operation, arguments)
                result = {"ok": True, "value": value}
            except fastjsonschema.JsonSchemaException:
                result = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "Arguments do not match the capability schema", "retryable": False}}
            except OperationError as exc:
                result = {"ok": False, "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}
            self.events.append({"id": f"env-{len(self.events) + 1:04d}", "source": "environment", "timestamp": now(), "kind": "tool_call", "data": {"operation": operation, "arguments": copy.deepcopy(arguments), "result": copy.deepcopy(result)}})
            return result

    def finalize(self):
        with self.lock:
            if self.evidence is None:
                self.frozen = True
                final = self.adapter.snapshot()
                checks, details = self.adapter.verify(self.initial, self.events)
                verification = [{"id": "check-" + name, "source": "verification", "timestamp": now(), "kind": "deterministic_check", "data": {"check": name, "passed": passed}} for name, passed in checks.items()]
                verification.append({"id": "verification-details", "source": "verification", "timestamp": now(), "kind": "verification_details", "data": details})
                self.evidence = {"initial": self.initial, "final": final, "events": copy.deepcopy(self.events), "verification": verification, "checks": checks, "tool_calls": self.calls}
            return copy.deepcopy(self.evidence)

    def close(self):
        self.adapter.close()
