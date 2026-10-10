"""Authenticated public simApps API and engine-only finalization."""
import json
import os
from http.server import ThreadingHTTPServer

from autowfbench.core.common import HTTPError, JsonHandler
from autowfbench.core.contracts import load_challenge
from autowfbench.runtime.environment.state import ChallengeEnvironment


def handler_for(env, run_token, admin_token):
    from autowfbench.runtime.environment.apps import catalog
    public = catalog(env.kind)
    paths = {op["path"]:op["operation"] for op in public["operations"]}
    class Handler(JsonHandler):
        def route(self, method):
            if method == "POST" and self.path == "/admin/finalize":
                self.auth(admin_token)
                return self.send(200, env.finalize())
            self.auth(run_token)
            if method == "GET" and self.path == "/apps":
                return self.send(200, public)
            if method == "POST" and self.path in paths:
                return self.send(200, env.execute(paths[self.path], self.body()))
            if method == "POST" and self.path == "/tools":
                data = self.body()
                return self.send(200, env.execute(data["operation"], data.get("arguments", {})))
            raise HTTPError(404, "Not found")
    return Handler


def serve(challenge_id, seed, host="127.0.0.1"):
    env = ChallengeEnvironment(load_challenge(challenge_id), seed)
    server = ThreadingHTTPServer((host, 0), handler_for(env, os.environ["AWB_RUN_TOKEN"], os.environ["AWB_ENV_ADMIN_TOKEN"]))
    print(json.dumps({"port": server.server_port}), flush=True)
    server.serve_forever()
