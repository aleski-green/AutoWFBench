"""Public container worker. Contains no scoring logic, credentials or expectations."""
import contextlib
from decimal import Decimal
import io
import json
import sys
import unittest


class BoundedOutput(io.StringIO):
    def write(self, text):
        if self.tell() + len(text) > 32000:
            raise ValueError("Application output limit exceeded")
        return super().write(text)


def invoke(request):
    from app import checkout
    if request["mode"] == "observe":
        return {"response": checkout(request["order"])}
    if request["mode"] == "batch":
        return {"responses": [checkout(order) for order in request["orders"]]}
    if request["mode"] == "tests":
        stream = BoundedOutput()
        suite = unittest.defaultTestLoader.discover("/app/tests", pattern="test_*.py")
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        return {"passed": result.wasSuccessful(), "tests_run": result.testsRun,
                "failures": len(result.failures), "errors": len(result.errors), "output": stream.getvalue()}
    raise ValueError("Unknown execution mode")


def main():
    request = json.load(sys.stdin)
    sys.path.insert(0, "/app")
    capture = BoundedOutput()
    try:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            result = invoke(request)
    except Exception as exc:
        result = {"execution_error": type(exc).__name__, "message": str(exc)[:1000]}
    print(json.dumps(result, default=lambda value: str(value) if isinstance(value, Decimal) else None, allow_nan=False))


if __name__ == "__main__":
    main()
