import json
from pathlib import Path


def convert_to_n8n(workflow):
    """
    Convert the Composer's internal workflow representation
    into an n8n-compatible workflow definition.

    Action nodes call the run-scoped AutoWFBench environment.
    AI nodes call the Composer reasoning endpoint.
    """

    n8n_nodes = []
    n8n_connections = {}

    node_name_by_id = {}

    # ----------------------------------------------
    # CONVERT NODES
    # ----------------------------------------------

    for index, node in enumerate(workflow.get("nodes", [])):

        node_id = node["id"]
        action = node["action"]
        node_type = node["type"]
        tool = node["tool"]
        operation = node["operation"]
        target = node["target"]

        node_name = f"{node_id} - {action}"
        node_name_by_id[node_id] = node_name

        # ------------------------------------------
        # TRIGGER
        # ------------------------------------------

        if node_type == "trigger":

            n8n_node = {
                "parameters": {},
                "id": str(node_id),
                "name": node_name,
                "type": "n8n-nodes-base.manualTrigger",
                "typeVersion": 1,
                "position": [250, index * 150]
            }

        # ------------------------------------------
        # AI REASONING
        # ------------------------------------------

        elif node_type == "ai":

            prompt = (
                f"Action: {action}\n"
                f"Operation: {operation}\n"
                f"Target: {target}\n\n"
                "Use the observations produced by the previous "
                "workflow steps as evidence."
            )

            n8n_node = {
                "parameters": {
                    "method": "POST",
                    "url": "={{$env.COMPOSER_LLM_ENDPOINT}}",
                    "sendBody": True,
                    "contentType": "raw",
                    "rawContentType": "application/json",
                    "body": json.dumps({
                        "prompt": prompt
                    })
                },
                "id": str(node_id),
                "name": node_name,
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [500, index * 150]
            }

        # ------------------------------------------
        # AUTOWFBENCH ENVIRONMENT ACTION
        # ------------------------------------------

        elif node_type == "action":

            # AutoWFBench capabilities are represented by
            # the operation/tool value, for example:
            #
            # incident.read
            # source.read
            # checkout.patch
            # tests.run

            payload = {
                "operation": operation,
                "arguments": {}
            }

            n8n_node = {
                "parameters": {
                    "method": "POST",

                    # The benchmark provides a different
                    # environment URL for every run.
                    "url": "={{$json.environment.base_url + '/tools'}}",

                    "sendHeaders": True,
                    "headerParameters": {
                        "parameters": [
                            {
                                "name": "Authorization",
                                "value": "={{'Bearer ' + $json.environment.access_token}}"
                            }
                        ]
                    },

                    "sendBody": True,
                    "contentType": "raw",
                    "rawContentType": "application/json",

                    "body": json.dumps(payload)
                },
                "id": str(node_id),
                "name": node_name,
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [500, index * 150]
            }

        else:
            raise ValueError(
                f"Unsupported node type: {node_type}"
            )

        n8n_nodes.append(n8n_node)

    # ----------------------------------------------
    # CONVERT CONNECTIONS
    # ----------------------------------------------

    for connection in workflow.get("connections", []):

        source_id = connection["from"]
        target_id = connection["to"]

        source_name = node_name_by_id[source_id]
        target_name = node_name_by_id[target_id]

        if source_name not in n8n_connections:
            n8n_connections[source_name] = {
                "main": [[]]
            }

        n8n_connections[source_name]["main"][0].append({
            "node": target_name,
            "type": "main",
            "index": 0
        })

    # ----------------------------------------------
    # FINAL N8N WORKFLOW
    # ----------------------------------------------

    return {
        "name": "AI Composer Generated Workflow",
        "nodes": n8n_nodes,
        "connections": n8n_connections,
        "settings": {}
    }


def export_n8n_workflow(workflow, output_path):
    """
    Convert and save the workflow as n8n JSON.
    """

    n8n_workflow = convert_to_n8n(workflow)

    output_path = Path(output_path)

    output_path.write_text(
        json.dumps(n8n_workflow, indent=2),
        encoding="utf-8"
    )

    return n8n_workflow