import json
import urllib.request
import uuid

from flask import Flask, request, jsonify

app = Flask(__name__)

jobs = {}


@app.route("/runs", methods=["POST"])
def start_run():
    data = request.get_json()

    execution_id = uuid.uuid4().hex

    jobs[execution_id] = {
        "status": "running",
        "run_id": data["run_id"]
    }

    # Forward the AutoWFBench run information to n8n
    n8n_url = "http://127.0.0.1:5678/webhook/checkout-v01"

    payload = json.dumps({
        "execution_id": execution_id,
        **data
    }).encode("utf-8")

    req = urllib.request.Request(
        n8n_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    urllib.request.urlopen(req, timeout=10).read()

    return jsonify({
        "execution_id": execution_id,
        "status": "running"
    }), 202


@app.route("/runs/<execution_id>", methods=["GET"])
def get_run(execution_id):
    job = jobs.get(execution_id)

    if not job:
        return jsonify({"error": "Unknown execution"}), 404

    return jsonify(job), 200


@app.route("/runs/<execution_id>/cancel", methods=["POST"])
def cancel_run(execution_id):
    if execution_id not in jobs:
        return jsonify({"error": "Unknown execution"}), 404

    jobs[execution_id]["status"] = "cancelled"

    return jsonify({"accepted": True}), 200

@app.route("/runs/<execution_id>/complete", methods=["POST"])
def complete_run(execution_id):
    job = jobs.get(execution_id)

    if not job:
        return jsonify({"error": "Unknown execution"}), 404

    data = request.get_json()

    if not data or "submission" not in data:
        return jsonify({"error": "Missing submission"}), 400

    submission = data["submission"]

    # The terminal API status must agree with submission.status
    if submission.get("status") not in ["completed", "failed"]:
        return jsonify({"error": "Invalid submission status"}), 400

    jobs[execution_id] = {
        "status": submission["status"],
        "run_id": job["run_id"],
        "submission": submission
    }

    return jsonify({
        "accepted": True,
        "execution_id": execution_id
    }), 200

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9301)