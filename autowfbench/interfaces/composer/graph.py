"""Compile a small, typed graph into native n8n nodes. No challenge-specific logic."""
from autowfbench.interfaces.composer.n8n import validate_workflow

FIELDS = {"name":{"type":"string"}, "kind":{"enum":["http","code","if"]}, "operation":{"type":"string"}, "code":{"type":"string"}, "expression":{"type":"string"}, "next":{"type":"array","maxItems":1,"items":{"type":"string"}}, "on_false":{"type":"array","maxItems":1,"items":{"type":"string"}}}
SCHEMA = {"type":"object", "properties":{"name":{"type":"string"},"nodes":{"type":"array","minItems":1,"maxItems":50,"items":{"type":"object","properties":FIELDS,"required":list(FIELDS),"additionalProperties":False}}}, "required":["name","nodes"], "additionalProperties":False}


def compile_graph(graph, apps):
    import fastjsonschema
    fastjsonschema.validate(SCHEMA, graph)
    nodes = [{"id":"start","name":"Start","type":"n8n-nodes-base.manualTrigger","typeVersion":1,"position":[0,0],"parameters":{}}, {"id":"context","name":"Context","type":"n8n-nodes-base.set","typeVersion":3.4,"position":[200,0],"parameters":{}}]
    edge = lambda name: {"node":name,"type":"main","index":0}
    connections = {"Start":{"main":[[edge("Context")]]}, "Context":{"main":[[edge(graph["nodes"][0]["name"])]]}}
    operations = {op["operation"]:op for op in apps["operations"]}
    for i,n in enumerate(graph["nodes"]):
        if n["name"] in ("Start","Context"):
            raise ValueError("Start and Context are reserved")
        if n["kind"] != "if" and n["on_false"]:
            raise ValueError("Only If nodes have a false branch")
        if n["kind"] == "http":
            op = operations[n["operation"]]
            kind,version = "httpRequest",4.2
            parameters = {"method":"POST","url":"={{ $('Context').first().json.environment.base_url + '"+op["path"]+"' }}", "sendHeaders":True,"headerParameters":{"parameters":[{"name":"Authorization","value":"={{ 'Bearer ' + $('Context').first().json.environment.access_token }}"}]},"sendBody":True,"specifyBody":"json","jsonBody":n["expression"],"options":{}}
        elif n["kind"] == "code":
            kind,version = "code",2
            parameters = {"mode":"runOnceForAllItems","jsCode":n["code"]}
        else:
            kind,version = "if",2.2
            parameters = {"conditions":{"options":{"caseSensitive":True,"leftValue":"","typeValidation":"strict","version":2},"conditions":[{"id":"condition","leftValue":n["expression"],"rightValue":True,"operator":{"type":"boolean","operation":"true","singleValue":True}}],"combinator":"and"},"options":{}}
        nodes.append({"id":f"node-{i}","name":n["name"],"type":"n8n-nodes-base."+kind,"typeVersion":version,"position":[400+i*200,0],"parameters":parameters})
        branches = [n["next"],n["on_false"]] if n["kind"]=="if" else [n["next"]]
        connections[n["name"]] = {"main":[[edge(name) for name in branch] for branch in branches]}
    return validate_workflow({"name":graph["name"],"nodes":nodes,"connections":connections,"settings":{"executionOrder":"v1"}}, apps)
