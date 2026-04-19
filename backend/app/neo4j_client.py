from neo4j import GraphDatabase

from app.config import load_config_decrypted

_driver = None


def get_neo4j_driver():
    global _driver
    if _driver is None:
        config = load_config_decrypted()
        neo4j = config.neo4j
        _driver = GraphDatabase.driver(neo4j.uri, auth=(neo4j.username, neo4j.password))
    return _driver


def close_neo4j_driver():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def run_cypher_write(driver, query: str, parameters: dict = None):
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as session:
        result = session.run(query, parameters or {})
        result.consume()


def run_cypher_read(driver, query: str, parameters: dict = None) -> list[dict]:
    """Execute a read-only Cypher query and return rows as dicts."""
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as s:
        result = s.run(query, parameters or {})
        return [dict(r) for r in result]


def run_cypher_read_graph(driver, query: str, parameters: dict = None) -> dict:
    """Execute a read-only Cypher query and return nodes + relationships."""
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as session:
        result = session.run(query, parameters or {})
        graph = result.graph()
        nodes = []
        for node in graph.nodes:
            nodes.append({
                "id": node.element_id,
                "labels": list(node.labels),
                "properties": dict(node),
            })
        relationships = []
        for rel in graph.relationships:
            relationships.append({
                "id": rel.element_id,
                "type": rel.type,
                "start_node_id": rel.start_node.element_id,
                "end_node_id": rel.end_node.element_id,
                "properties": dict(rel),
            })
        return {"nodes": nodes, "relationships": relationships}
