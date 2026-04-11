from fastapi import APIRouter, HTTPException
from openai import OpenAI

from app.config import load_config_decrypted
from app.cypher_validator import validate_read_only
from app.models import ExpandRequest, QueryRequest, QueryResponse
from app.neo4j_client import get_neo4j_driver, run_cypher_read_graph
from app.session import session

router = APIRouter(prefix="/api/query", tags=["query"])


@router.get("/entry-classes")
def get_entry_classes():
    """Return classes whose methods are NOT called by methods of other classes."""
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")

    cypher = """
        MATCH (c:Java:Class)-[:HAS_METHOD]->(m:Java:Method)
        WHERE NOT EXISTS {
            MATCH (other:Java:Class)-[:HAS_METHOD]->(caller:Java:Method)-[:CALLS]->(m)
            WHERE other <> c
        }
        WITH DISTINCT c
        MATCH (c)-[:HAS_METHOD]->(m:Java:Method)
        RETURN c.name AS className, elementId(c) AS classId,
               m.name AS methodName, elementId(m) AS methodId
        ORDER BY c.name, m.name
    """
    driver = get_neo4j_driver()
    try:
        config = load_config_decrypted()
        with driver.session(database=config.neo4j.database) as db_session:
            result = db_session.run(cypher)
            classes: dict[str, dict] = {}
            for record in result:
                cid = record["classId"]
                if cid not in classes:
                    classes[cid] = {
                        "id": cid,
                        "name": record["className"],
                        "methods": [],
                    }
                classes[cid]["methods"].append({
                    "id": record["methodId"],
                    "name": record["methodName"],
                })
            return list(classes.values())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    finally:
        pass

NEO4J_SCHEMA = """
## Code Metamodel (label: Java)
Node labels and their properties:
- Java:Repository: name, path
- Java:Module: name, path, detected_technologies (list of strings)
- Java:Package: full_name, name
- Java:Class: full_name, name, kind (class|interface|enum|record), is_abstract, is_test, file_path, visibility, source_code (full Java file), annotations (JSON), imports (list of FQN strings), star_imports (list of package strings), supertypes (JSON)
- Java:Method: full_name, name, return_type, parameters, is_static, is_abstract, visibility, start_line, end_line, annotations (JSON)

Relationships:
- (Java:Repository)-[:CONTAINS_MODULE]->(Java:Module)
- (Java:Module)-[:CONTAINS_PACKAGE]->(Java:Package)
- (Java:Package)-[:CONTAINS_CLASS]->(Java:Class)
- (Java:Class)-[:HAS_METHOD]->(Java:Method)
- (Java:Method)-[:CALLS]->(Java:Method)

## Architecture Metamodel (label: Arch)
Node labels and their properties:
- Arch:Microservice: name, technologies (list of strings like ["spring-web", "feign", "spring-data"])
- Arch:RESTInterface: name, base_path
- Arch:RESTEndpoint: path (URL path like "/api/users/{id}"), http_method (GET|POST|PUT|DELETE|PATCH), produces, consumes
- Arch:JMSDestination: name (queue or topic name)
- Arch:JMSListener: destination, selector, concurrency, container_factory
- Arch:JMSProducer: name
- Arch:ScheduledTask: cron, fixed_delay, fixed_rate, initial_delay, zone
- Arch:FeignClient: name, url, path, service_id
- Arch:FeignEndpoint: path (URL path), http_method (GET|POST|PUT|DELETE|PATCH)
- Arch:HTTPClient: name, client_type (RestTemplate|WebClient)
- Arch:Repository: name, entity_type, repo_type (JPA|Mongo|Redis|CRUD|Reactive|Elasticsearch)

Relationships:
- (Arch:RESTInterface)-[:HAS_ENDPOINT]->(Arch:RESTEndpoint)
- (Arch:FeignClient)-[:HAS_ENDPOINT]->(Arch:FeignEndpoint)
- (Arch:JMSListener)-[:LISTENS_ON]->(Arch:JMSDestination)
- (Arch:JMSProducer)-[:SENDS_TO]->(Arch:JMSDestination)

## Cross-metamodel relationships:
- (Arch:Microservice)-[:IMPLEMENTED_BY]->(Java:Repository)
- (Arch:RESTInterface)-[:IMPLEMENTED_BY]->(Java:Class)
- (Arch:RESTEndpoint)-[:IMPLEMENTED_BY]->(Java:Method)
- (Arch:JMSListener)-[:IMPLEMENTED_BY]->(Java:Method)
- (Arch:JMSProducer)-[:IMPLEMENTED_BY]->(Java:Class)
- (Arch:ScheduledTask)-[:IMPLEMENTED_BY]->(Java:Method)
- (Arch:FeignClient)-[:IMPLEMENTED_BY]->(Java:Class)
- (Arch:FeignEndpoint)-[:IMPLEMENTED_BY]->(Java:Method)
- (Arch:HTTPClient)-[:IMPLEMENTED_BY]->(Java:Class)
- (Arch:Repository)-[:IMPLEMENTED_BY]->(Java:Class)

## Data Flow Metamodel (label: Data)
Node labels and their properties:
- Data:Service: name, is_external
- Data:Endpoint: path, http_method (GET|POST|PUT|DELETE|PATCH), direction (inbound|outbound), service_name
- Data:Queue: name, type (queue|topic)
- Data:Database: name, technology (sql|mongo|redis|elasticsearch)
- Data:DataModel: full_name, name, kind (dto|entity|message|request|response)

Relationships:
- (Data:Service)-[:EXPOSES]->(Data:Endpoint)          inbound REST endpoints
- (Data:Service)-[:CALLS]->(Data:Endpoint)             outbound Feign endpoints
- (Data:Service)-[:PRODUCES]->(Data:Queue)
- (Data:Service)-[:CONSUMES]->(Data:Queue)
- (Data:Service)-[:READS_FROM]->(Data:Database)
- (Data:Service)-[:WRITES_TO]->(Data:Database)
- (Data:Endpoint)-[:ACCEPTS]->(Data:DataModel)         request body type
- (Data:Endpoint)-[:RETURNS]->(Data:DataModel)          response body type
- (Data:Queue)-[:CARRIES]->(Data:DataModel)             message type

## Cross-metamodel relationships (Data -> Arch/Java):
- (Data:Service)-[:MAPS_TO]->(Arch:Microservice)
- (Data:Endpoint)-[:MAPS_TO]->(Arch:RESTEndpoint) or (Arch:FeignEndpoint)
- (Data:Queue)-[:MAPS_TO]->(Arch:JMSDestination)
- (Data:Database)-[:MAPS_TO]->(Arch:Repository)
- (Data:DataModel)-[:MAPS_TO]->(Java:Class)

## Tooling Metamodel (label: Tooling) — Git history and Jira references
Node labels and their properties:
- Tooling:Repository: name, path
- Tooling:Commit: hash (short 12-char), full_hash, date (ISO-8601), author_name, author_email, message, files_changed (list of file paths), issue_keys (list of Jira keys like "PROJ-123"), branches (list of branch names this commit belongs to)
- Tooling:Branch: name, repo_name
- Tooling:JiraTicket: key (e.g. "PROJ-123"), project (e.g. "PROJ")

Relationships:
- (Tooling:Repository)-[:HAS_COMMIT]->(Tooling:Commit)
- (Tooling:Commit)-[:PARENT {ord}]->(Tooling:Commit)          ord 0 = first parent, ord 1 = merge parent
- (Tooling:Commit)-[:REFERENCES]->(Tooling:JiraTicket)
- (Tooling:Repository)-[:HAS_BRANCH]->(Tooling:Branch)
- (Tooling:Branch)-[:TIP]->(Tooling:Commit)                   newest commit on the branch
- (Tooling:Branch)-[:FIRST]->(Tooling:Commit)                  oldest commit on the branch

## Cross-metamodel relationships (Tooling -> Java):
- (Tooling:Repository)-[:SAME_REPO]->(Java:Repository)         links git repo to code repo

Important notes about the Tooling domain:
- A commit's branches property is a list of branch names; to filter commits on a branch, use WHERE "branch-name" IN c.branches
- To walk the first-parent chain of a branch (linear history), follow PARENT edges with ord=0
- The PARENT relationship goes from child to parent (newer to older): (child)-[:PARENT]->(parent)
- Commits have a short hash (12 chars) and full_hash; use hash for display, full_hash for exact matching
- files_changed is a list of relative file paths modified in that commit

## Facet Metamodel (label: Facet) — User-defined classification taxonomy
Node labels and their properties:
- Facet:Facet: name, description
- Facet:Value: name, label, ordinal

Relationships:
- (Facet:Facet)-[:HAS_VALUE]->(Facet:Value)         root-level values of a facet
- (Facet:Value)-[:NARROWER]->(Facet:Value)           hierarchical refinement (parent to child)
- (any node)-[:CLASSIFIED_AS]->(Facet:Value)         tags any data node with a facet value

Note: CLASSIFIED_AS can originate from any domain node (Java, Arch, Data, Tooling). Facet is a cross-cutting overlay for the entire graph.

Method full_name format: "package.ClassName.methodName"
Class full_name format: "package.ClassName"
Note: imports is a native Neo4j list property — use WHERE 'com.example.Foo' IN c.imports or UNWIND c.imports AS imp
"""

SYSTEM_PROMPT = f"""You are a Cypher query generator for a Neo4j database containing software project analysis data with five metamodels: Code (Java), Architecture (Arch), Data Flow (Data), Tooling (git history + Jira), and Facet (classification taxonomy).

{NEO4J_SCHEMA}

Rules:
1. Generate ONLY read-only Cypher queries (MATCH, RETURN, WITH, WHERE, ORDER BY, LIMIT, OPTIONAL MATCH).
2. NEVER use CREATE, MERGE, DELETE, SET, REMOVE, or DROP.
3. ALWAYS RETURN full nodes and relationships (variables), NEVER just properties. The result is rendered as a graph visualization, not a table. For example: RETURN c, hm, m — not RETURN c.name, m.name.
4. When returning methods, ALWAYS also return their parent Class and the HAS_METHOD relationship.
5. Limit results to 100 nodes maximum.
6. Respond with ONLY the Cypher query, no explanation, no markdown fences.
7. Each domain has its own label prefix: Java (Java:Class, Java:Method), Arch (Arch:RESTInterface), Data (Data:Service), Tooling (Tooling:Commit, Tooling:Branch). Always use the correct domain prefix.
8. Use Data domain nodes for questions about data flow, services, endpoints, queues, databases, and data models. Use MAPS_TO relationships to cross-reference between Data and Arch/Java domains.
9. For questions about git commits, branches, commit history, authors, or Jira tickets, use the Tooling domain — NOT the Java domain. Tooling:Repository is the git repo; Java:Repository is the code analysis repo. They are linked by SAME_REPO but are different nodes.
10. When filtering commits by branch, use the branches list property: WHERE "branch-name" IN c.branches. Do NOT try to match on Branch nodes for this — the branches property on Commit is the canonical way.

Example queries:
- "Show all classes in package core":
  MATCH (p:Java:Package)-[:CONTAINS_CLASS]->(c:Java:Class) WHERE p.full_name CONTAINS 'core' OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m:Java:Method) RETURN p, c, hm, m LIMIT 100

- "Show classes that import OrderService":
  MATCH (c:Java:Class) WHERE any(imp IN c.imports WHERE imp ENDS WITH '.OrderService') RETURN c

- "What methods does UserService have?":
  MATCH (c:Java:Class {{name: 'UserService'}})-[hm:HAS_METHOD]->(m:Java:Method) RETURN c, hm, m

- "Show methods that call processOrder":
  MATCH (caller:Java:Method)-[call:CALLS]->(callee:Java:Method {{name: 'processOrder'}}) MATCH (cc:Java:Class)-[hm1:HAS_METHOD]->(caller) MATCH (tc:Java:Class)-[hm2:HAS_METHOD]->(callee) RETURN cc, hm1, caller, call, callee, hm2, tc

- "Show all REST endpoints":
  MATCH (ri:Arch:RESTInterface)-[he:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint) RETURN ri, he, ep

- "Show all GET endpoints":
  MATCH (ri:Arch:RESTInterface)-[he:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint {{http_method: 'GET'}}) RETURN ri, he, ep

- "Show REST endpoints with their implementing classes and methods":
  MATCH (ri:Arch:RESTInterface)-[ib1:IMPLEMENTED_BY]->(c:Java:Class) MATCH (ri)-[he:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint)-[ib2:IMPLEMENTED_BY]->(m:Java:Method) RETURN ri, ib1, c, he, ep, ib2, m

- "Show all architecture nodes":
  MATCH (n:Arch) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 100

- "Show all JMS listeners":
  MATCH (l:Arch:JMSListener)-[ib:IMPLEMENTED_BY]->(m:Java:Method) MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m) OPTIONAL MATCH (l)-[lo:LISTENS_ON]->(d:Arch:JMSDestination) RETURN l, ib, m, c, hm, lo, d

- "Show message flow for a queue":
  MATCH (d:Arch:JMSDestination {{name: 'order-events'}}) OPTIONAL MATCH (l:Arch:JMSListener)-[lo:LISTENS_ON]->(d) OPTIONAL MATCH (l)-[ib1:IMPLEMENTED_BY]->(m:Java:Method) OPTIONAL MATCH (p:Arch:JMSProducer)-[st:SENDS_TO]->(d) OPTIONAL MATCH (p)-[ib2:IMPLEMENTED_BY]->(c:Java:Class) RETURN d, l, lo, ib1, m, p, st, ib2, c

- "Show all JMS destinations with producers and listeners":
  MATCH (d:Arch:JMSDestination) OPTIONAL MATCH (l:Arch:JMSListener)-[lo:LISTENS_ON]->(d) OPTIONAL MATCH (p:Arch:JMSProducer)-[st:SENDS_TO]->(d) RETURN d, l, lo, p, st

- "Show all microservices and their technologies":
  MATCH (ms:Arch:Microservice) OPTIONAL MATCH (ms)-[ib:IMPLEMENTED_BY]->(r:Java:Repository) RETURN ms, ib, r

- "Show all scheduled tasks":
  MATCH (st:Arch:ScheduledTask)-[ib:IMPLEMENTED_BY]->(m:Java:Method) MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m) RETURN st, ib, m, c, hm

- "Show all Feign clients and their endpoints":
  MATCH (fc:Arch:FeignClient)-[ib:IMPLEMENTED_BY]->(c:Java:Class) OPTIONAL MATCH (fc)-[he:HAS_ENDPOINT]->(fe:Arch:FeignEndpoint) RETURN fc, ib, c, he, fe

- "Show all HTTP client classes":
  MATCH (hc:Arch:HTTPClient)-[ib:IMPLEMENTED_BY]->(c:Java:Class) RETURN hc, ib, c

- "Show all Spring Data repositories":
  MATCH (r:Arch:Repository)-[ib:IMPLEMENTED_BY]->(c:Java:Class) RETURN r, ib, c

- "Show which classes use RestTemplate":
  MATCH (hc:Arch:HTTPClient {{client_type: 'RestTemplate'}})-[ib:IMPLEMENTED_BY]->(c:Java:Class) RETURN hc, ib, c

- "Show all data services and their endpoints":
  MATCH (ds:Data:Service) OPTIONAL MATCH (ds)-[exp:EXPOSES]->(ie:Data:Endpoint) OPTIONAL MATCH (ds)-[call:CALLS]->(oe:Data:Endpoint) RETURN ds, exp, ie, call, oe

- "Show the data flow for a service":
  MATCH (ds:Data:Service {{name: 'my-service'}}) OPTIONAL MATCH (ds)-[r1]->(n:Data) OPTIONAL MATCH (n)-[r2]->(dm:Data:DataModel) RETURN ds, r1, n, r2, dm

- "Show all data models":
  MATCH (dm:Data:DataModel) OPTIONAL MATCH (dm)-[mt:MAPS_TO]->(c:Java:Class) RETURN dm, mt, c

- "Show which queues a service produces to":
  MATCH (ds:Data:Service)-[p:PRODUCES]->(q:Data:Queue) RETURN ds, p, q

- "Show all databases and their services":
  MATCH (ds:Data:Service)-[r:READS_FROM|WRITES_TO]->(db:Data:Database) RETURN ds, r, db

- "Show all commits on the develop branch for communication-service":
  MATCH (r:Tooling:Repository {{name: 'communication-service'}})-[hc:HAS_COMMIT]->(c:Tooling:Commit) WHERE "develop" IN c.branches RETURN r, hc, c LIMIT 100

- "Show the first-parent commit chain for the main branch":
  MATCH (c:Tooling:Commit)-[p:PARENT {{ord: 0}}]->(parent:Tooling:Commit) WHERE "main" IN c.branches AND "main" IN parent.branches RETURN c, p, parent LIMIT 100

- "Show all branches for a repository":
  MATCH (r:Tooling:Repository {{name: 'my-repo'}})-[hb:HAS_BRANCH]->(b:Tooling:Branch)-[tip:TIP]->(c:Tooling:Commit) RETURN r, hb, b, tip, c

- "Show commits by a specific author":
  MATCH (r:Tooling:Repository)-[hc:HAS_COMMIT]->(c:Tooling:Commit) WHERE c.author_name CONTAINS 'John' RETURN r, hc, c ORDER BY c.date DESC LIMIT 100

- "Show commits that reference a Jira ticket":
  MATCH (c:Tooling:Commit)-[ref:REFERENCES]->(t:Tooling:JiraTicket {{key: 'PROJ-123'}}) RETURN c, ref, t

- "Show all Jira tickets referenced in a repository":
  MATCH (r:Tooling:Repository)-[:HAS_COMMIT]->(c:Tooling:Commit)-[ref:REFERENCES]->(t:Tooling:JiraTicket) WHERE r.name = 'my-repo' RETURN c, ref, t LIMIT 100

- "Show recent commits across all repos":
  MATCH (r:Tooling:Repository)-[hc:HAS_COMMIT]->(c:Tooling:Commit) RETURN r, hc, c ORDER BY c.date DESC LIMIT 50

- "Show which commits changed files related to a class":
  MATCH (c:Tooling:Commit) WHERE any(f IN c.files_changed WHERE f CONTAINS 'OrderService') RETURN c ORDER BY c.date DESC LIMIT 50

- "Link git repo to code repo":
  MATCH (tr:Tooling:Repository)-[sr:SAME_REPO]->(jr:Java:Repository) RETURN tr, sr, jr

- "Show all facets and their values":
  MATCH (f:Facet:Facet)-[hv:HAS_VALUE]->(v:Facet:Value) OPTIONAL MATCH (v)-[nr:NARROWER*]->(child:Facet:Value) RETURN f, hv, v, nr, child

- "Show nodes classified under a value":
  MATCH (n)-[ca:CLASSIFIED_AS]->(v:Facet:Value {{name: 'Backend'}}) RETURN n, ca, v LIMIT 100

- "Show all Java classes classified as Backend in Q1-2024":
  MATCH (c:Java:Class)-[ca1:CLASSIFIED_AS]->(v1:Facet:Value {{name: 'Backend'}}) MATCH (c)-[ca2:CLASSIFIED_AS]->(v2:Facet:Value {{name: 'Q1-2024'}}) RETURN c, ca1, v1, ca2, v2
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
            MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m:Java:Method)
            WHERE elementId(m) IN $ids
            RETURN c, hm, m
        """, {"ids": list(orphan_ids)})
    finally:
        pass

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
        pass

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
        MATCH (start:Java:Method)
        WHERE elementId(start) = $node_id
        OPTIONAL MATCH (start)-[:CALLS*1..{depth}]->(m:Java:Method)
        WITH start, collect(DISTINCT m) AS downstream
        WITH [start] + downstream AS methods
        UNWIND methods AS m
        MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(target:Java:Method)
        WHERE target IN methods
        RETURN c, hm, m, call, target
    """,
    "upstream_calls": """
        MATCH (target:Java:Method)
        WHERE elementId(target) = $node_id
        OPTIONAL MATCH (m:Java:Method)-[:CALLS*1..{depth}]->(target)
        WITH target, collect(DISTINCT m) AS upstream
        WITH [target] + upstream AS methods
        UNWIND methods AS m
        MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(callee:Java:Method)
        WHERE callee IN methods
        RETURN c, hm, m, call, callee
    """,
    "show_methods": """
        MATCH (c:Java:Class)-[hm:HAS_METHOD]->(m:Java:Method)
        WHERE elementId(c) = $node_id
        RETURN c, hm, m
    """,
    "class_downstream": """
        MATCH (c:Java:Class)-[:HAS_METHOD]->(start:Java:Method)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (start)-[:CALLS*1..{depth}]->(m:Java:Method)
        WITH collect(DISTINCT start) + collect(DISTINCT m) AS all_methods
        WITH [x IN all_methods WHERE x IS NOT NULL] AS methods
        UNWIND methods AS m
        MATCH (cls:Java:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(target:Java:Method)
        WHERE target IN methods
        RETURN cls, hm, m, call, target
    """,
    "class_upstream": """
        MATCH (c:Java:Class)-[:HAS_METHOD]->(target:Java:Method)
        WHERE elementId(c) = $node_id
        OPTIONAL MATCH (m:Java:Method)-[:CALLS*1..{depth}]->(target)
        WITH collect(DISTINCT target) + collect(DISTINCT m) AS all_methods
        WITH [x IN all_methods WHERE x IS NOT NULL] AS methods
        UNWIND methods AS m
        MATCH (cls:Java:Class)-[hm:HAS_METHOD]->(m)
        OPTIONAL MATCH (m)-[call:CALLS]->(callee:Java:Method)
        WHERE callee IN methods
        RETURN cls, hm, m, call, callee
    """,
    "show_imports": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        UNWIND c.imports AS imp_fqn
        MATCH (t:Java:Class {full_name: imp_fqn})
        RETURN c, t
    """,
    "show_imported_by": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (src:Java:Class)
        WHERE c.full_name IN src.imports
        RETURN src, c
    """,
    "show_rest_endpoints": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (ri:Arch:RESTInterface)-[ib:IMPLEMENTED_BY]->(c)
        OPTIONAL MATCH (ri)-[he:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint)
        OPTIONAL MATCH (ep)-[ib2:IMPLEMENTED_BY]->(m:Java:Method)
        OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m)
        RETURN ri, ib, c, he, ep, ib2, m, hm
    """,
    "show_rest_implementation": """
        MATCH (ri:Arch:RESTInterface)
        WHERE elementId(ri) = $node_id
        MATCH (ri)-[ib:IMPLEMENTED_BY]->(c:Java:Class)
        OPTIONAL MATCH (ri)-[he:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint)
        OPTIONAL MATCH (ep)-[ib2:IMPLEMENTED_BY]->(m:Java:Method)
        OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m)
        RETURN ri, ib, c, he, ep, ib2, m, hm
    """,
    "show_jms_listeners": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (c)-[hm:HAS_METHOD]->(m:Java:Method)
              <-[ib:IMPLEMENTED_BY]-(l:Arch:JMSListener)
        OPTIONAL MATCH (l)-[lo:LISTENS_ON]->(d:Arch:JMSDestination)
        RETURN c, hm, m, ib, l, lo, d
    """,
    "show_jms_destination": """
        MATCH (d:Arch:JMSDestination)
        WHERE elementId(d) = $node_id
        OPTIONAL MATCH (l:Arch:JMSListener)-[lo:LISTENS_ON]->(d)
        OPTIONAL MATCH (l)-[ib1:IMPLEMENTED_BY]->(m:Java:Method)
        OPTIONAL MATCH (lc:Java:Class)-[hm1:HAS_METHOD]->(m)
        OPTIONAL MATCH (p:Arch:JMSProducer)-[st:SENDS_TO]->(d)
        OPTIONAL MATCH (p)-[ib2:IMPLEMENTED_BY]->(pc:Java:Class)
        RETURN d, l, lo, ib1, m, lc, hm1, p, st, ib2, pc
    """,
    "show_jms_producers": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (p:Arch:JMSProducer)-[ib:IMPLEMENTED_BY]->(c)
        OPTIONAL MATCH (p)-[st:SENDS_TO]->(d:Arch:JMSDestination)
        RETURN c, p, ib, st, d
    """,
    "show_scheduled_tasks": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (c)-[hm:HAS_METHOD]->(m:Java:Method)
              <-[ib:IMPLEMENTED_BY]-(st:Arch:ScheduledTask)
        RETURN c, hm, m, ib, st
    """,
    "show_feign_endpoints": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (fc:Arch:FeignClient)-[ib:IMPLEMENTED_BY]->(c)
        OPTIONAL MATCH (fc)-[he:HAS_ENDPOINT]->(fe:Arch:FeignEndpoint)
        OPTIONAL MATCH (fe)-[ib2:IMPLEMENTED_BY]->(m:Java:Method)
        OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m)
        RETURN fc, ib, c, he, fe, ib2, m, hm
    """,
    "show_feign_implementation": """
        MATCH (fc:Arch:FeignClient)
        WHERE elementId(fc) = $node_id
        MATCH (fc)-[ib:IMPLEMENTED_BY]->(c:Java:Class)
        OPTIONAL MATCH (fc)-[he:HAS_ENDPOINT]->(fe:Arch:FeignEndpoint)
        OPTIONAL MATCH (fe)-[ib2:IMPLEMENTED_BY]->(m:Java:Method)
        OPTIONAL MATCH (c)-[hm:HAS_METHOD]->(m)
        RETURN fc, ib, c, he, fe, ib2, m, hm
    """,
    "show_http_clients": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (hc:Arch:HTTPClient)-[ib:IMPLEMENTED_BY]->(c)
        RETURN c, hc, ib
    """,
    "show_repository": """
        MATCH (c:Java:Class)
        WHERE elementId(c) = $node_id
        MATCH (r:Arch:Repository)-[ib:IMPLEMENTED_BY]->(c)
        RETURN c, r, ib
    """,
    "show_repository_implementation": """
        MATCH (r:Arch:Repository)
        WHERE elementId(r) = $node_id
        MATCH (r)-[ib:IMPLEMENTED_BY]->(c:Java:Class)
        RETURN r, ib, c
    """,
    "show_microservice": """
        MATCH (ms:Arch:Microservice)
        WHERE elementId(ms) = $node_id
        MATCH (ms)-[ib:IMPLEMENTED_BY]->(r:Java:Repository)
        OPTIONAL MATCH (r)-[cm:CONTAINS_MODULE]->(m:Java:Module)
        RETURN ms, ib, r, cm, m
    """,
    "show_node": """
        MATCH (n)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN n, r, m
        LIMIT 100
    """,
    "show_java_node": """
        MATCH (n:Java)
        WHERE elementId(n) = $node_id
          AND (n:Class OR n:Method)
        OPTIONAL MATCH (n)-[r]-(m:Java)
        WHERE m:Class OR m:Method
        RETURN n, r, m
        LIMIT 100
    """,
    "show_arch_node": """
        MATCH (n:Arch)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH (n)-[r]-(m:Arch)
        RETURN n, r, m
        LIMIT 100
    """,
    "arch_downstream": """
        MATCH (n:Arch)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH (n)-[r]->(m:Arch)
        RETURN n, r, m
        LIMIT 100
    """,
    "arch_upstream": """
        MATCH (n:Arch)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH (n)<-[r]-(m:Arch)
        RETURN n, r, m
        LIMIT 100
    """,
    "show_data_service": """
        MATCH (ds:Data:Service)
        WHERE elementId(ds) = $node_id
        OPTIONAL MATCH (ds)-[r]->(n:Data)
        OPTIONAL MATCH (n)-[r2]->(dm:Data:DataModel)
        RETURN ds, r, n, r2, dm
        LIMIT 100
    """,
    "show_data_endpoint": """
        MATCH (de:Data:Endpoint)
        WHERE elementId(de) = $node_id
        OPTIONAL MATCH (de)-[r1:ACCEPTS]->(req:Data:DataModel)
        OPTIONAL MATCH (de)-[r2:RETURNS]->(res:Data:DataModel)
        OPTIONAL MATCH (de)-[mt:MAPS_TO]->(arch)
        RETURN de, r1, req, r2, res, mt, arch
        LIMIT 100
    """,
    "show_data_flow": """
        MATCH (ds:Data:Service)
        WHERE elementId(ds) = $node_id
        OPTIONAL MATCH (ds)-[r1:EXPOSES]->(ie:Data:Endpoint)
        OPTIONAL MATCH (ds)-[r2:CALLS]->(oe:Data:Endpoint)
        OPTIONAL MATCH (ds)-[r3:PRODUCES]->(pq:Data:Queue)
        OPTIONAL MATCH (ds)-[r4:CONSUMES]->(cq:Data:Queue)
        OPTIONAL MATCH (ds)-[r5:READS_FROM]->(rdb:Data:Database)
        OPTIONAL MATCH (ds)-[r6:WRITES_TO]->(wdb:Data:Database)
        OPTIONAL MATCH (ie)-[r7:ACCEPTS|RETURNS]->(dm1:Data:DataModel)
        OPTIONAL MATCH (oe)-[r8:ACCEPTS|RETURNS]->(dm2:Data:DataModel)
        OPTIONAL MATCH (pq)-[r9:CARRIES]->(dm3:Data:DataModel)
        OPTIONAL MATCH (cq)-[r10:CARRIES]->(dm4:Data:DataModel)
        RETURN ds, r1, ie, r2, oe, r3, pq, r4, cq,
               r5, rdb, r6, wdb,
               r7, dm1, r8, dm2, r9, dm3, r10, dm4
        LIMIT 100
    """,
    "show_data_node": """
        MATCH (n:Data)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH (n)-[r]-(m:Data)
        RETURN n, r, m
        LIMIT 100
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
        pass

    _enrich_orphan_methods(graph_data)

    return QueryResponse(
        cypher=cypher,
        nodes=graph_data["nodes"],
        relationships=graph_data["relationships"],
    )
