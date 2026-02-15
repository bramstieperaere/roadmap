from fastapi import APIRouter, HTTPException
from openai import OpenAI

from app.config import load_config_decrypted
from app.cypher_validator import validate_read_only
from app.models import ExpandRequest, QueryRequest, QueryResponse
from app.neo4j_client import get_neo4j_driver, run_cypher_read_graph
from app.session import session

router = APIRouter(prefix="/api/query", tags=["query"])

NEO4J_SCHEMA = """
Node labels and their properties:
- Module: name, path, repository
- Package: full_name, name
- Class: full_name, name, kind (class|interface|enum|record), is_abstract, is_test, file_path, visibility, source_code
- Method: full_name, name, return_type, parameters, is_static, is_abstract, visibility, start_line, end_line

Relationships:
- (Module)-[:CONTAINS_PACKAGE]->(Package)
- (Package)-[:CONTAINS_CLASS]->(Class)
- (Class)-[:HAS_METHOD]->(Method)
- (Class)-[:IMPORTS]->(Class)
- (Method)-[:CALLS]->(Method)

Method full_name format: "package.ClassName.methodName"
Class full_name format: "package.ClassName"
"""

SYSTEM_PROMPT = f"""You are a Cypher query generator for a Neo4j database containing Java source code analysis data.

{NEO4J_SCHEMA}

Rules:
1. Generate ONLY read-only Cypher queries (MATCH, RETURN, WITH, WHERE, ORDER BY, LIMIT, OPTIONAL MATCH).
2. NEVER use CREATE, MERGE, DELETE, SET, REMOVE, or DROP.
3. Always RETURN full nodes and relationships, not just properties, so the graph can be visualized.
4. When returning methods, ALWAYS also return their parent Class and the HAS_METHOD relationship.
5. Limit results to 100 nodes maximum.
6. Respond with ONLY the Cypher query, no explanation, no markdown fences.

Example queries:
- "Show all classes in package core":
  MATCH (p:Package)-[:CONTAINS_CLASS]->(c:Class) WHERE p.full_name CONTAINS 'core' OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m:Method) RETURN p, c, hm, m LIMIT 100

- "Show classes that import OrderService":
  MATCH (source:Class)-[imp:IMPORTS]->(target:Class {{name: 'OrderService'}}) RETURN source, imp, target

- "What methods does UserService have?":
  MATCH (c:Class {{name: 'UserService'}})-[hm:HAS_METHOD]->(m:Method) RETURN c, hm, m

- "Show methods that call processOrder":
  MATCH (caller:Method)-[call:CALLS]->(callee:Method {{name: 'processOrder'}}) MATCH (cc:Class)-[hm1:HAS_METHOD]->(caller) MATCH (tc:Class)-[hm2:HAS_METHOD]->(callee) RETURN cc, hm1, caller, call, callee, hm2, tc
"""


def _find_query_provider(config):
    for task_type in ("cypher_generation", "repository_analysis"):
        task = next(
            (t for t in config.ai_tasks if t.task_type == task_type), None)
        if task:
            provider = next(
                (p for p in config.ai_providers
                 if p.name == task.provider_name), None)
            if provider:
                return provider
    if config.ai_providers:
        return config.ai_providers[0]
    raise HTTPException(
        status_code=400, detail="No AI provider configured")


def _enrich_orphan_methods(graph_data: dict):
    """For any Method node without a parent Class, fetch the parent."""
    method_ids = {
        n["id"] for n in graph_data["nodes"]
        if "Method" in n["labels"]
    }
    has_parent = {
        r["end_node_id"] for r in graph_data["relationships"]
        if r["type"] == "HAS_METHOD"
    }
    orphan_ids = method_ids - has_parent
    if not orphan_ids:
        return

    driver = get_neo4j_driver()
    try:
        enrichment = run_cypher_read_graph(driver, """
            MATCH (c:Class)-[hm:HAS_METHOD]->(m:Method)
            WHERE elementId(m) IN $ids
            RETURN c, hm, m
        """, {"ids": list(orphan_ids)})
    finally:
        driver.close()

    existing_node_ids = {n["id"] for n in graph_data["nodes"]}
    existing_rel_ids = {r["id"] for r in graph_data["relationships"]}
    for node in enrichment["nodes"]:
        if node["id"] not in existing_node_ids:
            graph_data["nodes"].append(node)
            existing_node_ids.add(node["id"])
    for rel in enrichment["relationships"]:
        if rel["id"] not in existing_rel_ids:
            graph_data["relationships"].append(rel)
            existing_rel_ids.add(rel["id"])


@router.post("", response_model=QueryResponse)
def execute_query(request: QueryRequest):
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")

    config = load_config_decrypted()
    provider = _find_query_provider(config)

    # Step 1: Generate Cypher from natural language
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    try:
        response = client.chat.completions.create(
            model=provider.default_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": request.question},
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        cypher = response.choices[0].message.content.strip()
        if cypher.startswith("```"):
            cypher = cypher.split("\n", 1)[1]
            cypher = cypher.rsplit("```", 1)[0].strip()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"AI query generation failed: {e}")

    # Step 2: Validate read-only
    is_safe, error_msg = validate_read_only(cypher)
    if not is_safe:
        return QueryResponse(
            cypher=cypher, nodes=[], relationships=[], error=error_msg)

    # Step 3: Execute against Neo4j
    driver = get_neo4j_driver()
    try:
        graph_data = run_cypher_read_graph(driver, cypher)
    except Exception as e:
        return QueryResponse(
            cypher=cypher, nodes=[], relationships=[],
            error=f"Cypher execution failed: {e}")
    finally:
        driver.close()

    # Step 4: Enrich orphan methods with parent Class
    _enrich_orphan_methods(graph_data)

    return QueryResponse(
        cypher=cypher,
        nodes=graph_data["nodes"],
        relationships=graph_data["relationships"],
    )


# ----- Expand operations -----

_EXPAND_QUERIES = {
    "downstream_calls": """
        MATCH (start:Method)
        WHERE elementId(start) = $node_id
        OPTIONAL MATCH (start)-[:CALLS*1..{depth}]->(m:Method)
        WITH start, collect(DISTINCT m) AS downstream
        WITH [start] + downstream AS methods
        UNWIND methods AS m
        MATCH (c:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(target:Method)
        WHERE target IN methods
        RETURN c, hm, m, call, target
    """,
    "upstream_calls": """
        MATCH (target:Method)
        WHERE elementId(target) = $node_id
        OPTIONAL MATCH (m:Method)-[:CALLS*1..{depth}]->(target)
        WITH target, collect(DISTINCT m) AS upstream
        WITH [target] + upstream AS methods
        UNWIND methods AS m
        MATCH (c:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(callee:Method)
        WHERE callee IN methods
        RETURN c, hm, m, call, callee
    """,
    "show_methods": """
        MATCH (c:Class)-[hm:HAS_METHOD]->(m:Method)
        WHERE elementId(c) = $node_id
        RETURN c, hm, m
    """,
    "class_downstream": """
        MATCH (c:Class)-[:HAS_METHOD]->(start:Method)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (start)-[:CALLS*1..{depth}]->(m:Method)
        WITH collect(DISTINCT start) + collect(DISTINCT m) AS all_methods
        WITH [x IN all_methods WHERE x IS NOT NULL] AS methods
        UNWIND methods AS m
        MATCH (cls:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(target:Method)
        WHERE target IN methods
        RETURN cls, hm, m, call, target
    """,
    "class_upstream": """
        MATCH (c:Class)-[:HAS_METHOD]->(target:Method)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (m:Method)-[:CALLS*1..{depth}]->(target)
        WITH collect(DISTINCT target) + collect(DISTINCT m) AS all_methods
        WITH [x IN all_methods WHERE x IS NOT NULL] AS methods
        UNWIND methods AS m
        MATCH (cls:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(callee:Method)
        WHERE callee IN methods
        RETURN cls, hm, m, call, callee
    """,
    "show_imports": """
        MATCH (c:Class)-[imp:IMPORTS]->(t:Class)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (c)-[hm1:HAS_METHOD]->(m1:Method)
        OPTIONAL MATCH (t)-[hm2:HAS_METHOD]->(m2:Method)
        RETURN c, imp, t, hm1, m1, hm2, m2
    """,
    "show_imported_by": """
        MATCH (src:Class)-[imp:IMPORTS]->(c:Class)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (src)-[hm1:HAS_METHOD]->(m1:Method)
        OPTIONAL MATCH (c)-[hm2:HAS_METHOD]->(m2:Method)
        RETURN src, imp, c, hm1, m1, hm2, m2
    """,
}


@router.post("/expand", response_model=QueryResponse)
def expand_node(request: ExpandRequest):
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")

    print(f"[EXPAND] op={request.operation} node_id={request.node_id}", flush=True)

    template = _EXPAND_QUERIES.get(request.operation)
    if not template:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown operation: {request.operation}. "
            f"Valid: {', '.join(_EXPAND_QUERIES.keys())}")

    depth = min(request.depth, 5)
    cypher = template.format(depth=depth)

    driver = get_neo4j_driver()
    try:
        graph_data = run_cypher_read_graph(
            driver, cypher, {"node_id": request.node_id})
        print(f"[EXPAND] result: {len(graph_data['nodes'])} nodes, "
              f"{len(graph_data['relationships'])} rels", flush=True)
    except Exception as e:
        print(f"[EXPAND] ERROR: {e}", flush=True)
        return QueryResponse(
            cypher=cypher, nodes=[], relationships=[],
            error=f"Expansion failed: {e}")
    finally:
        driver.close()

    _enrich_orphan_methods(graph_data)

    return QueryResponse(
        cypher=cypher,
        nodes=graph_data["nodes"],
        relationships=graph_data["relationships"],
    )
