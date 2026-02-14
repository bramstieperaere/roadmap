from neo4j import GraphDatabase

from app.config import load_config_decrypted


def get_neo4j_driver():
    config = load_config_decrypted()
    neo4j = config.neo4j
    return GraphDatabase.driver(neo4j.uri, auth=(neo4j.username, neo4j.password))


def run_cypher_write(driver, query: str, parameters: dict = None):
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as session:
        session.run(query, parameters or {})
