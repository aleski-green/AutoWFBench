"""Execute application code only inside disposable, restricted Docker containers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import threading
import time
import uuid

from .base import OperationError


class DockerSandbox:
    def __init__(self, workspace, image, timeout=15):
        self.workspace = Path(workspace).resolve()
        self.image, self.timeout = image, timeout
        self.image_id = None
        self.active = set()
        self.lock = threading.Lock()

    def _docker(self, args, **kwargs):
        # Only Docker connection configuration is inherited by the host CLI.
        # No host environment variables are passed into the container.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")}
        deadline = time.monotonic() + kwargs.pop("timeout", 10)
        try:
            with tempfile.TemporaryFile() as stdin:
                stdin.write(kwargs.pop("input", "").encode())
                stdin.seek(0)
                with subprocess.Popen(["docker", *args], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env) as proc:
                    output = bytearray()
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(proc.stdout, selectors.EVENT_READ)
                            while True:
                                remaining = deadline - time.monotonic()
                                if remaining <= 0 or not selector.select(remaining):
                                    raise OperationError("EXECUTION_TIMEOUT", "Docker operation exceeded its time limit", True)
                                chunk = os.read(proc.stdout.fileno(), 8192)
                                if not chunk:
                                    break
                                output.extend(chunk)
                                if len(output) > 200000:
                                    raise OperationError("OUTPUT_LIMIT", "Application output limit exceeded")
                        proc.wait(timeout=max(.01, deadline - time.monotonic()))
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                    return subprocess.CompletedProcess(proc.args, proc.returncode, output.decode("utf-8", errors="replace"), "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperationError("SANDBOX_UNAVAILABLE", "Docker is unavailable or timed out", True) from exc

    def preflight(self):
        if self.image_id:
            return self.image_id
        if self._docker(["info", "--format", "{{.ServerVersion}}"], timeout=2).returncode:
            raise OperationError("SANDBOX_UNAVAILABLE", "Docker daemon is unavailable; start Docker Desktop", True)
        result = self._docker(["image", "inspect", self.image, "--format", "{{.Id}}"], timeout=2)
        image_id = result.stdout.strip()
        if result.returncode or not image_id.startswith("sha256:"):
            raise OperationError("SANDBOX_UNAVAILABLE", "Required container image is missing; provision the image before execution", True)
        self.image_id = image_id
        return image_id

    def run(self, request):
        image_id = self.preflight()
        name = "awb-checkout-" + uuid.uuid4().hex
        runner = Path(__file__).with_name("worker.py").resolve()
        args = ["create", "--rm", "--name", name, "--pull=never", "--network=none",
                "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user=65534:65534", "--pids-limit=32", "--memory=128m", "--cpus=1",
                "--ulimit", "fsize=262144:262144", "--log-driver=none",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir=/app",
                "--mount", f"type=bind,src={self.workspace},dst=/app,readonly",
                "--mount", f"type=bind,src={runner},dst=/runner.py,readonly",
                "-i", image_id, "python", "-I", "-B", "/runner.py"]
        with self.lock:
            self.active.add(name)
        try:
            # Obtain the container's creation acknowledgement before starting
            # the attached execution. Killing a combined `docker run` while it
            # is starting can race the daemon's asynchronous container creation.
            # Creation and execution still share the original time budget.
            deadline = time.monotonic() + self.timeout
            created = self._docker(args, timeout=self.timeout)
            if created.returncode:
                raise OperationError("EXECUTION_FAILED", "Application container creation failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationError("EXECUTION_TIMEOUT", "Docker operation exceeded its time limit", True)
            result = self._docker(["start", "--attach", "--interactive", name], input=json.dumps(request), timeout=remaining)
            if result.returncode or len(result.stdout) > 200000:
                raise OperationError("EXECUTION_FAILED", "Application execution failed or exceeded output limits")
            try:
                value = json.loads(result.stdout)
            except ValueError as exc:
                raise OperationError("EXECUTION_FAILED", "Application returned invalid execution output") from exc
            if not isinstance(value, dict):
                raise OperationError("EXECUTION_FAILED", "Application returned an invalid result")
            return value
        finally:
            self._remove(name)

    def _remove(self, name):
        result = self._docker(["rm", "-f", name], timeout=2)
        # --rm may already have removed a completed container. Other failures
        # must stay tracked so close() can retry, rather than silently leaking.
        if result.returncode and "No such container" not in result.stdout:
            raise OperationError("CLEANUP_FAILED", "Unable to confirm application container removal", True)
        with self.lock:
            self.active.discard(name)

    def close(self):
        with self.lock:
            names = tuple(self.active)
        for name in names:
            self._remove(name)
