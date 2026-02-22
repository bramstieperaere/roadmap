from fastapi import APIRouter, HTTPException

from app.config import load_config_decrypted
from app.neo4j_client import get_neo4j_driver
from app.session import session

router = APIRouter(prefix="/api/browse", tags=["browse"])


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


# ----- Technical hierarchy: label -> (relationship, child label) -----

_TECHNICAL_LEVELS = {
    "root": {
        "query": """
            MATCH (n:Java:Repository)
            RETURN elementId(n) AS id, labels(n) AS labels,
                   n.name AS name, n.full_name AS detail,
                   EXISTS { MATCH (n)-[:CONTAINS_MODULE]->() } AS has_children,
                   'repository' AS kind
            ORDER BY n.name
        """,
    },
    "Repository": {
        "query": """
            MATCH (parent)
            WHERE elementId(parent) = $parent_id
            MATCH (parent)-[:CONTAINS_MODULE]->(n:Java:Module)
            RETURN elementId(n) AS id, labels(n) AS labels,
                   n.name AS name, n.name AS detail,
                   EXISTS { MATCH (n)-[:CONTAINS_PACKAGE]->() } AS has_children,
                   'module' AS kind
            ORDER BY n.name
        """,
    },
    "Module": {
        "query": """
            MATCH (parent)
            WHERE elementId(parent) = $parent_id
            MATCH (parent)-[:CONTAINS_PACKAGE]->(n:Java:Package)
            RETURN elementId(n) AS id, labels(n) AS labels,
                   n.full_name AS name, n.full_name AS detail,
                   EXISTS { MATCH (n)-[:CONTAINS_CLASS]->() } AS has_children,
                   'package' AS kind
            ORDER BY n.full_name
        """,
    },
    "Package": {
        "query": """
            MATCH (parent)
            WHERE elementId(parent) = $parent_id
            MATCH (parent)-[:CONTAINS_CLASS]->(n:Java:Class)
            RETURN elementId(n) AS id, labels(n) AS labels,
                   n.name AS name, n.full_name AS detail,
                   EXISTS { MATCH (n)-[:HAS_METHOD]->() } AS has_children,
                   n.kind AS kind
            ORDER BY n.name
        """,
    },
    "Class": {
        "query": """
            MATCH (parent)
            WHERE elementId(parent) = $parent_id
            MATCH (parent)-[:HAS_METHOD]->(n:Java:Method)
            RETURN elementId(n) AS id, labels(n) AS labels,
                   n.name AS name, n.full_name AS detail,
                   false AS has_children,
                   'method' AS kind
            ORDER BY n.name
        """,
    },
}

# ----- Architecture hierarchy: Microservice -> categories -> Arch nodes -----

_ARCH_CATEGORIES = [
    {"key": "rest-apis", "name": "REST APIs", "kind": "category"},
    {"key": "feign-clients", "name": "Feign Clients", "kind": "category"},
    {"key": "jms", "name": "JMS", "kind": "category"},
    {"key": "scheduled-tasks", "name": "Scheduled Tasks",
     "kind": "category"},
    {"key": "http-clients", "name": "HTTP Clients", "kind": "category"},
    {"key": "repositories", "name": "Repositories", "kind": "category"},
]

# Common Cypher prefix: from Microservice down to Java classes
_MS_TO_CLASS = """
    MATCH (ms:Arch:Microservice) WHERE elementId(ms) = $ms_id
    MATCH (ms)-[:IMPLEMENTED_BY]->(repo:Java:Repository)
    MATCH (repo)-[:CONTAINS_MODULE]->(:Java:Module)
          -[:CONTAINS_PACKAGE]->(:Java:Package)
          -[:CONTAINS_CLASS]->(cls:Java:Class)
    WITH DISTINCT cls
"""

# Count queries per category, scoped to a microservice ($ms_id)
_MS_COUNT_QUERIES = {
    "rest-apis": _MS_TO_CLASS + """
        MATCH (:Arch:RESTInterface)-[:IMPLEMENTED_BY]->(cls)
        RETURN count(cls) AS c
    """,
    "feign-clients": _MS_TO_CLASS + """
        MATCH (:Arch:FeignClient)-[:IMPLEMENTED_BY]->(cls)
        RETURN count(cls) AS c
    """,
    "jms": None,  # handled specially in _count_jms_for_ms
    "scheduled-tasks": _MS_TO_CLASS + """
        MATCH (cls)-[:HAS_METHOD]->(meth:Java:Method)
              <-[:IMPLEMENTED_BY]-(:Arch:ScheduledTask)
        RETURN count(meth) AS c
    """,
    "http-clients": _MS_TO_CLASS + """
        MATCH (:Arch:HTTPClient)-[:IMPLEMENTED_BY]->(cls)
        RETURN count(cls) AS c
    """,
    "repositories": _MS_TO_CLASS + """
        MATCH (:Arch:Repository)-[:IMPLEMENTED_BY]->(cls)
        RETURN count(cls) AS c
    """,
}

# Data queries per category, scoped to a microservice ($ms_id)
_MS_VIRTUAL_QUERIES = {
    "rest-apis": _MS_TO_CLASS + """
        MATCH (n:Arch:RESTInterface)-[:IMPLEMENTED_BY]->(cls)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels,
               n.name + CASE WHEN n.base_path IS NOT NULL
                   AND n.base_path <> ''
                   THEN ' [' + n.base_path + ']' ELSE '' END AS name,
               EXISTS { MATCH (n)-[:HAS_ENDPOINT]->() } AS has_children,
               'rest-interface' AS kind
        ORDER BY name
    """,
    "feign-clients": _MS_TO_CLASS + """
        MATCH (n:Arch:FeignClient)-[:IMPLEMENTED_BY]->(cls)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels,
               n.name AS name,
               EXISTS { MATCH (n)-[:HAS_ENDPOINT]->() } AS has_children,
               'feign-client' AS kind
        ORDER BY name
    """,
    "jms": None,  # handled specially in _get_jms_for_ms
    "scheduled-tasks": _MS_TO_CLASS + """
        MATCH (cls)-[:HAS_METHOD]->(meth:Java:Method)
              <-[:IMPLEMENTED_BY]-(n:Arch:ScheduledTask)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels,
               meth.name + CASE
                   WHEN n.cron IS NOT NULL THEN ' [cron: ' + n.cron + ']'
                   WHEN n.fixed_rate IS NOT NULL
                       THEN ' [rate: ' + n.fixed_rate + 'ms]'
                   WHEN n.fixed_delay IS NOT NULL
                       THEN ' [delay: ' + n.fixed_delay + 'ms]'
                   ELSE '' END AS name,
               false AS has_children,
               'scheduled-task' AS kind
        ORDER BY name
    """,
    "http-clients": _MS_TO_CLASS + """
        MATCH (n:Arch:HTTPClient)-[:IMPLEMENTED_BY]->(cls)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels,
               n.name + ' (' + n.client_type + ')' AS name,
               false AS has_children,
               'http-client' AS kind
        ORDER BY name
    """,
    "repositories": _MS_TO_CLASS + """
        MATCH (n:Arch:Repository)-[:IMPLEMENTED_BY]->(cls)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels,
               n.name AS name,
               false AS has_children,
               'repository' AS kind
        ORDER BY name
    """,
}

_ARCH_NODE_LEVELS = {
    "RESTInterface": """
        MATCH (parent:Arch:RESTInterface)
        WHERE elementId(parent) = $parent_id
        MATCH (parent)-[:HAS_ENDPOINT]->(n:Arch:RESTEndpoint)
        RETURN elementId(n) AS id, labels(n) AS labels,
               n.http_method + ' ' + n.path AS name,
               false AS has_children,
               'rest-endpoint' AS kind
        ORDER BY n.path, n.http_method
    """,
    "FeignClient": """
        MATCH (parent:Arch:FeignClient)
        WHERE elementId(parent) = $parent_id
        MATCH (parent)-[:HAS_ENDPOINT]->(n:Arch:FeignEndpoint)
        RETURN elementId(n) AS id, labels(n) AS labels,
               n.http_method + ' ' + n.path AS name,
               false AS has_children,
               'feign-endpoint' AS kind
        ORDER BY n.path, n.http_method
    """,
    "JMSDestination": """
        MATCH (parent:Arch:JMSDestination)
        WHERE elementId(parent) = $parent_id
        OPTIONAL MATCH (l:Arch:JMSListener)-[:LISTENS_ON]->(parent)
        OPTIONAL MATCH (l)-[:IMPLEMENTED_BY]->(lm:Java:Method)
        WITH parent,
             COLLECT(DISTINCT CASE WHEN l IS NOT NULL THEN {
                 id: elementId(l), labels: labels(l),
                 name: 'Listener: ' + COALESCE(lm.name, '?'),
                 kind: 'jms-listener'
             } END) AS listeners
        OPTIONAL MATCH (p:Arch:JMSProducer)-[:SENDS_TO]->(parent)
        WITH listeners,
             COLLECT(DISTINCT CASE WHEN p IS NOT NULL THEN {
                 id: elementId(p), labels: labels(p),
                 name: 'Producer: ' + p.name,
                 kind: 'jms-producer'
             } END) AS producers
        UNWIND (listeners + producers) AS item
        WITH item WHERE item IS NOT NULL
        RETURN item.id AS id, item.labels AS labels,
               item.name AS name,
               false AS has_children,
               item.kind AS kind
    """,
}

# Map perspective name -> level definitions
_PERSPECTIVES = {
    "technical": _TECHNICAL_LEVELS,
}


def _detect_level(labels: list[str]) -> str:
    """Detect which hierarchy level a node is at from its labels."""
    for label in ("Class", "Package", "Module", "Repository"):
        if label in labels:
            return label
    return "root"


def _detect_arch_level(labels: list[str]) -> str:
    """Detect which arch hierarchy level a node is at."""
    for label in ("RESTInterface", "FeignClient", "JMSDestination"):
        if label in labels:
            return label
    return ""


@router.get("/search")
def search_nodes(q: str, limit: int = 20):
    """Search nodes by name across all types."""
    _require_unlocked()

    if not q or len(q) < 2:
        return []

    cypher = """
        MATCH (n)
        WHERE (n:Java OR n:Arch)
          AND n.name IS NOT NULL
          AND toLower(n.name) CONTAINS toLower($q)
        RETURN elementId(n) AS id, labels(n) AS labels,
               n.name AS name,
               COALESCE(n.full_name, n.name) AS detail
        ORDER BY
            CASE WHEN toLower(n.name) STARTS WITH toLower($q)
                 THEN 0 ELSE 1 END,
            n.name
        LIMIT $limit
    """

    driver = get_neo4j_driver()
    try:
        config = load_config_decrypted()
        with driver.session(database=config.neo4j.database) as db_session:
            result = db_session.run(cypher, {"q": q, "limit": limit})
            return [
                {
                    "id": r["id"],
                    "labels": r["labels"],
                    "name": r["name"],
                    "detail": r["detail"],
                }
                for r in result
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")
    finally:
        driver.close()


@router.get("/tree")
def get_tree_children(perspective: str = "technical",
                      parent_id: str | None = None):
    """Get children for a tree node in the given perspective."""
    _require_unlocked()

    # Architecture perspective has special handling
    if perspective == "architecture":
        return _get_arch_tree(parent_id)

    levels = _PERSPECTIVES.get(perspective)
    if not levels:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown perspective: {perspective}. "
            f"Valid: technical, architecture")

    if not parent_id:
        level_def = levels.get("root")
    else:
        # Detect level from parent node labels
        parent_labels = _get_node_labels(parent_id)
        level_key = _detect_level(parent_labels)
        level_def = levels.get(level_key)

    if not level_def:
        return []

    driver = get_neo4j_driver()
    try:
        config = load_config_decrypted()
        with driver.session(database=config.neo4j.database) as db_session:
            params = {"parent_id": parent_id} if parent_id else {}
            result = db_session.run(level_def["query"], params)
            return [
                {
                    "id": r["id"],
                    "labels": r["labels"],
                    "name": r["name"],
                    "has_children": r["has_children"],
                    "kind": r["kind"] or "",
                }
                for r in result
            ]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Tree query failed: {e}")
    finally:
        driver.close()


def _get_arch_tree(parent_id: str | None) -> list[dict]:
    """Handle architecture perspective tree queries."""
    driver = get_neo4j_driver()
    try:
        config = load_config_decrypted()
        with driver.session(database=config.neo4j.database) as db_session:
            if not parent_id:
                # Root: return Microservice nodes
                result = db_session.run("""
                    MATCH (ms:Arch:Microservice)
                    RETURN elementId(ms) AS id, labels(ms) AS labels,
                           ms.name AS name, true AS has_children,
                           'microservice' AS kind
                    ORDER BY ms.name
                """)
                return [
                    {
                        "id": r["id"],
                        "labels": r["labels"],
                        "name": r["name"],
                        "has_children": r["has_children"],
                        "kind": r["kind"],
                    }
                    for r in result
                ]

            if parent_id.startswith("virtual:"):
                # Virtual category scoped to a microservice
                # Format: "virtual:<category>@<ms_element_id>"
                return _expand_virtual_category(db_session, parent_id)

            # Real node — check labels
            parent_labels = _get_node_labels_with_session(
                db_session, parent_id)

            if "Microservice" in parent_labels:
                # Microservice -> virtual categories with counts
                return _get_ms_categories(db_session, parent_id)

            level_key = _detect_arch_level(parent_labels)
            query = _ARCH_NODE_LEVELS.get(level_key)
            if not query:
                return []
            result = db_session.run(query, {"parent_id": parent_id})
            return [
                {
                    "id": r["id"],
                    "labels": r["labels"],
                    "name": r["name"],
                    "has_children": r["has_children"],
                    "kind": r["kind"] or "",
                }
                for r in result
            ]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Tree query failed: {e}")
    finally:
        driver.close()


def _get_ms_categories(db_session, ms_id: str) -> list[dict]:
    """Return virtual categories for a microservice, hiding empty ones."""
    categories = []
    for cat in _ARCH_CATEGORIES:
        count_q = _MS_COUNT_QUERIES.get(cat["key"])
        if count_q:
            result = db_session.run(count_q, {"ms_id": ms_id})
            record = result.single()
            count = record["c"] if record else 0
        elif cat["key"] == "jms":
            count = _count_jms_for_ms(db_session, ms_id)
        else:
            count = 1  # no count query means always show
        if count == 0:
            continue
        categories.append({
            "id": f"virtual:{cat['key']}@{ms_id}",
            "labels": ["Virtual"],
            "name": cat["name"],
            "has_children": True,
            "kind": cat["kind"],
        })
    return categories


def _count_jms_for_ms(db_session, ms_id: str) -> int:
    """Count JMS destinations connected to a microservice via two paths."""
    # Destinations via listeners (method-level)
    r1 = db_session.run(_MS_TO_CLASS + """
        MATCH (cls)-[:HAS_METHOD]->(meth:Java:Method)
              <-[:IMPLEMENTED_BY]-(:Arch:JMSListener)
              -[:LISTENS_ON]->(dest:Arch:JMSDestination)
        RETURN COLLECT(DISTINCT elementId(dest)) AS ids
    """, {"ms_id": ms_id})
    ids = set(r1.single()["ids"] or [])
    # Destinations via producers (class-level)
    r2 = db_session.run(_MS_TO_CLASS + """
        MATCH (p:Arch:JMSProducer)-[:IMPLEMENTED_BY]->(cls)
        MATCH (p)-[:SENDS_TO]->(dest:Arch:JMSDestination)
        RETURN COLLECT(DISTINCT elementId(dest)) AS ids
    """, {"ms_id": ms_id})
    ids.update(r2.single()["ids"] or [])
    return len(ids)


def _get_jms_for_ms(db_session, ms_id: str) -> list[dict]:
    """Get JMS destinations connected to a microservice."""
    # Destinations via listeners
    r1 = db_session.run(_MS_TO_CLASS + """
        MATCH (cls)-[:HAS_METHOD]->(meth:Java:Method)
              <-[:IMPLEMENTED_BY]-(:Arch:JMSListener)
              -[:LISTENS_ON]->(dest:Arch:JMSDestination)
        RETURN DISTINCT elementId(dest) AS id, labels(dest) AS labels,
               dest.name AS name, true AS has_children,
               'jms-destination' AS kind
    """, {"ms_id": ms_id})
    by_id = {r["id"]: dict(r) for r in r1}
    # Destinations via producers
    r2 = db_session.run(_MS_TO_CLASS + """
        MATCH (p:Arch:JMSProducer)-[:IMPLEMENTED_BY]->(cls)
        MATCH (p)-[:SENDS_TO]->(dest:Arch:JMSDestination)
        RETURN DISTINCT elementId(dest) AS id, labels(dest) AS labels,
               dest.name AS name, true AS has_children,
               'jms-destination' AS kind
    """, {"ms_id": ms_id})
    for r in r2:
        by_id.setdefault(r["id"], dict(r))
    return sorted(by_id.values(), key=lambda d: d["name"])


def _expand_virtual_category(db_session, virtual_id: str) -> list[dict]:
    """Expand a virtual category scoped to a microservice."""
    rest = virtual_id.removeprefix("virtual:")
    if "@" not in rest:
        return []
    cat_key, ms_id = rest.split("@", 1)
    if cat_key == "jms":
        return _get_jms_for_ms(db_session, ms_id)
    query = _MS_VIRTUAL_QUERIES.get(cat_key)
    if not query:
        return []
    result = db_session.run(query, {"ms_id": ms_id})
    return [
        {
            "id": r["id"],
            "labels": r["labels"],
            "name": r["name"],
            "has_children": r["has_children"],
            "kind": r["kind"] or "",
        }
        for r in result
    ]


def _get_node_labels(node_id: str) -> list[str]:
    """Fetch labels for a node by elementId."""
    driver = get_neo4j_driver()
    try:
        config = load_config_decrypted()
        with driver.session(database=config.neo4j.database) as db_session:
            return _get_node_labels_with_session(db_session, node_id)
    finally:
        driver.close()


def _get_node_labels_with_session(db_session, node_id: str) -> list[str]:
    """Fetch labels using an existing session."""
    result = db_session.run("""
        MATCH (n) WHERE elementId(n) = $id
        RETURN labels(n) AS labels
    """, {"id": node_id})
    record = result.single()
    return record["labels"] if record else []
