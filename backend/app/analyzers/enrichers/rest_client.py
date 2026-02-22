from app.analyzers.enrichers.base import TechnologyEnricher
from app.neo4j_client import run_cypher_write

_REST_TEMPLATE_FQN = "org.springframework.web.client.RestTemplate"
_REST_TEMPLATE_PKG = "org.springframework.web.client"
_WEBCLIENT_FQN = \
    "org.springframework.web.reactive.function.client.WebClient"
_WEBCLIENT_PKG = "org.springframework.web.reactive.function.client"


class RestClientEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        stats = {"http_clients": 0}
        self._process_client(
            stats, _REST_TEMPLATE_FQN, _REST_TEMPLATE_PKG, "RestTemplate")
        self._process_client(
            stats, _WEBCLIENT_FQN, _WEBCLIENT_PKG, "WebClient")

        self.log_info(
            f"REST Clients: {stats['http_clients']} HTTP clients")
        return stats

    def _process_client(self, stats: dict, fqn: str, pkg: str,
                        client_type: str):
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                WHERE $fqn IN c.imports
                   OR any(sp IN c.star_imports WHERE sp = $pkg)
                RETURN c.full_name AS full_name,
                       c.name AS name
            """, {
                "module_name": self.module_name,
                "fqn": fqn,
                "pkg": pkg,
            })

            for record in result:
                self.log_info(
                    f"  {client_type}: {record['name']}")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (c:Java:Class {full_name: $full_name})
                        CREATE (hc:Arch:HTTPClient {
                            name: $name,
                            client_type: $client_type
                        })
                        CREATE (hc)-[:IMPLEMENTED_BY]->(c)
                    """, {
                        "full_name": record["full_name"],
                        "name": record["name"],
                        "client_type": client_type,
                    })
                    stats["http_clients"] += 1
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create HTTPClient: {e}")
