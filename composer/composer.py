import json
import sys

from pathlib import Path
from groq import Groq
from n8n_adapter import export_n8n_workflow


# --------------------------------------------------
# AI CLIENT
# --------------------------------------------------

# Uses the GROQ_API_KEY environment variable
client = Groq()


# --------------------------------------------------
# COMPOSER
# --------------------------------------------------

def compose_workflow(problem, capabilities):
    """
    Takes a natural-language problem and the capabilities
    available in the current environment, then asks AI
    to design a workflow for solving it.
    """

    capability_text = "\n".join(
        f"- {capability}" for capability in capabilities
    )

    prompt = f"""
You are an AI Workflow Composer.

The user will describe a problem or goal, NOT the workflow.

Your job is to design the workflow required to solve that problem.

You must decide:
- what steps are required
- the correct order of the steps
- which steps are deterministic
- which steps require AI reasoning
- what capability each executable step requires

Do not expect the user to specify the workflow steps.
Design the workflow yourself from the problem.


AVAILABLE CAPABILITIES

You may ONLY use these environment capabilities:

{capability_text}

Do not invent capabilities outside this list.


WORKFLOW FORMAT

Return ONLY valid JSON in this format:

{{
  "nodes": [
    {{
      "id": 1,
      "action": "short description of the action",
      "type": "trigger, action, or ai",
      "tool": null,
      "operation": "environment capability to invoke",
      "target": "what the operation should act on"
    }}
  ],

  "connections": [
    {{
      "from": 1,
      "to": 2
    }}
  ]
}}


RULES

- Use type "trigger" for the starting event.

- A trigger node does not execute an environment capability.
  A trigger node MUST use null for "tool".

- For an executable environment action, use type "action".

- For an action node, "tool" MUST contain exactly one capability
  from AVAILABLE CAPABILITIES.

- For an action node, "operation" should contain the capability
  being invoked.

- Final answers, reports, summaries, and submission artifacts
  should be produced by an AI node when they require generated
  content. Do NOT misuse an environment capability to create
  an artifact unless that capability explicitly supports it.

- Only use an environment capability for the purpose described
  by that capability. Do not use a capability merely because
  no better capability is available.

- If the required final output is an artifact or report,
  generate its content in an AI node. The execution adapter
  will include that content in the final submission.
  
- AI reasoning nodes do not invoke an environment capability
  directly and MUST use null for "tool".

- Prefer deterministic environment capabilities when AI
  reasoning is not necessary.

- Break the problem into small executable steps.

- Every node should represent one clear responsibility.

- The workflow must be operationally complete.

- Do not skip intermediate steps required to move
  from one state to another.

- If information is required before a step can execute,
  explicitly include a step that retrieves it.

- If a decision or diagnosis leads to a change,
  explicitly include the step that performs that change.

- If a change must be verified,
  explicitly include a verification step.

- Separate reasoning from execution when they are
  different operations.

- Do not invent filenames, commands, URLs, resources,
  or capabilities that were not provided.

- Do not simply repeat the problem statement.

Return JSON only.
Do not include explanations.


PROBLEM:

{problem}
"""

    # ----------------------------------------------
    # CALL THE COMPOSER AI
    # ----------------------------------------------

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    ai_output = response.choices[0].message.content

    # Convert AI JSON response into Python dictionary
    workflow = json.loads(ai_output)

    return workflow


# --------------------------------------------------
# WORKFLOW VALIDATOR
# --------------------------------------------------

def validate_workflow(workflow, capabilities):
    """
    Checks whether the AI-generated workflow follows
    the workflow rules and only uses capabilities
    provided by the environment.
    """

    allowed_capabilities = set(capabilities)

    errors = []

    nodes = workflow.get("nodes", [])
    connections = workflow.get("connections", [])

    node_ids = {node.get("id") for node in nodes}

    # ----------------------------------------------
    # VALIDATE NODES
    # ----------------------------------------------

    for node in nodes:

        node_id = node.get("id")
        tool = node.get("tool")
        operation = node.get("operation")
        node_type = node.get("type")

        required_fields = [
            "id",
            "action",
            "type",
            "tool",
            "operation",
            "target"
        ]

        # Check required fields
        for field in required_fields:

            if field not in node:
                errors.append(
                    f"Node {node_id} is missing '{field}'"
                )

        # ------------------------------------------
        # VALIDATE NODE TYPE
        # ------------------------------------------

        if node_type not in ["trigger", "action", "ai"]:
            errors.append(
                f"Node {node_id} has invalid type: {node_type}"
            )

        # ------------------------------------------
        # VALIDATE CAPABILITY
        # ------------------------------------------

        if node_type == "trigger":

            if tool is not None:
                errors.append(
                    f"Trigger node {node_id} must have tool set to null"
                )

        elif node_type == "ai":

            if tool is not None:
                errors.append(
                    f"AI node {node_id} must have tool set to null"
                )

        elif node_type == "action":

            if tool not in allowed_capabilities:
                errors.append(
                    f"Node {node_id} uses unsupported capability: {tool}"
                )

            if operation != tool:
                errors.append(
                    f"Node {node_id} operation must match capability: {tool}"
                )

    # ----------------------------------------------
    # VALIDATE CONNECTIONS
    # ----------------------------------------------

    for connection in connections:

        source = connection.get("from")
        target = connection.get("to")

        if source not in node_ids:
            errors.append(
                f"Connection references missing node: {source}"
            )

        if target not in node_ids:
            errors.append(
                f"Connection references missing node: {target}"
            )

    return errors


# --------------------------------------------------
# MAIN
# --------------------------------------------------

if __name__ == "__main__":

    # Make sure an environment path was provided
    if len(sys.argv) < 2:
        print("Usage: python3 composer.py <environment_path>")
        sys.exit(1)

    environment_path = Path(sys.argv[1])

    task_file = environment_path / "task.md"

    if not task_file.exists():
        print(f"ERROR: task.md not found in {environment_path}")
        sys.exit(1)

    problem = task_file.read_text(encoding="utf-8")

    print("\nLoaded task from:")
    print(task_file)

    print("\nTask:")
    print(problem)

    # Temporary capabilities for local testing.
    # Later these will come directly from the
    # AutoWFBench POST /runs request.
    capabilities = [
        "incident.read",
        "source.read",
        "checkout.patch",
        "tests.run"
    ]

    print("\nAvailable capabilities:")
    for capability in capabilities:
        print("-", capability)

    # Generate workflow
    workflow = compose_workflow(
        problem,
        capabilities
    )

    print("\nGenerated workflow:")
    print(json.dumps(workflow, indent=2))

    # Validate workflow
    errors = validate_workflow(
        workflow,
        capabilities
    )

    print("\nValidation:")

    if errors:
        print("INVALID WORKFLOW")

        for error in errors:
            print("-", error)

    else:
        print("VALID WORKFLOW")

        output_file = Path("generated_n8n_workflow.json")

        export_n8n_workflow(
            workflow,
            output_file
        )

        print("\nGenerated n8n workflow:")
        print(output_file.resolve())