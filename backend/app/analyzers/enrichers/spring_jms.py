import re

from app.analyzers.enrichers.base import TechnologyEnricher, parse_json
from app.neo4j_client import run_cypher_write

_JMS_LISTENER_FQN = "org.springframework.jms.annotation.JmsListener"
_JMS_TEMPLATE_FQN = "org.springframework.jms.core.JmsTemplate"
_JMS_TEMPLATE_PKG = "org.springframework.jms.core"

# JmsTemplate method names that send messages
_SEND_METHODS = ("send", "convertAndSend", "sendAndReceive")


class SpringJmsEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        stats = {"listeners": 0, "producers": 0, "destinations": 0}
        destinations_seen: set[str] = set()

        self._process_listeners(stats, destinations_seen)
        self._process_producers(stats, destinations_seen)

        self.log_info(
            f"Spring JMS: {stats['listeners']} listeners, "
            f"{stats['producers']} producers, "
            f"{stats['destinations']} destinations")
        return stats

    # ----- Listeners -----

    def _process_listeners(self, stats: dict,
                           destinations_seen: set[str]):
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      -[:HAS_METHOD]->(m:Java:Method)
                RETURN c.full_name AS class_full_name,
                       c.name AS class_name,
                       m.full_name AS method_full_name,
                       m.name AS method_name,
                       m.annotations AS method_annotations
            """, {"module_name": self.module_name})

            for record in result:
                annotations = parse_json(record["method_annotations"])
                jms_ann = None
                for ann in annotations:
                    if ann.get("name") == _JMS_LISTENER_FQN:
                        jms_ann = ann
                        break
                if not jms_ann:
                    continue

                args = jms_ann.get("arguments") or {}
                destination = args.get("destination", "")
                if isinstance(destination, list):
                    destination = destination[0] if destination else ""
                destination = destination.strip('"').strip("'")

                selector = args.get("selector", "")
                concurrency = args.get("concurrency", "")
                container_factory = args.get("containerFactory", "")

                self.log_info(
                    f"  JMS Listener: {record['method_name']} "
                    f"on '{destination}' "
                    f"(class={record['class_name']})")

                try:
                    if destination and destination not in destinations_seen:
                        self._merge_destination(destination)
                        destinations_seen.add(destination)
                        stats["destinations"] += 1

                    run_cypher_write(self.driver, """
                        MATCH (m:Java:Method {
                            full_name: $method_full_name})
                        CREATE (l:Arch:JMSListener {
                            destination: $destination,
                            selector: $selector,
                            concurrency: $concurrency,
                            container_factory: $container_factory
                        })
                        CREATE (l)-[:IMPLEMENTED_BY]->(m)
                    """, {
                        "method_full_name": record["method_full_name"],
                        "destination": destination,
                        "selector": selector,
                        "concurrency": concurrency,
                        "container_factory": container_factory,
                    })

                    if destination:
                        run_cypher_write(self.driver, """
                            MATCH (l:Arch:JMSListener)
                                  -[:IMPLEMENTED_BY]->
                                  (:Java:Method {
                                      full_name: $method_full_name})
                            MATCH (d:Arch:JMSDestination {
                                name: $destination})
                            CREATE (l)-[:LISTENS_ON]->(d)
                        """, {
                            "method_full_name":
                                record["method_full_name"],
                            "destination": destination,
                        })

                    stats["listeners"] += 1
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create JMSListener: {e}")

    # ----- Producers -----

    def _process_producers(self, stats: dict,
                           destinations_seen: set[str]):
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                WHERE $jms_template IN c.imports
                   OR any(sp IN c.star_imports
                          WHERE sp = $jms_pkg)
                RETURN c.full_name AS full_name,
                       c.name AS name,
                       c.source_code AS source_code
            """, {
                "module_name": self.module_name,
                "jms_template": _JMS_TEMPLATE_FQN,
                "jms_pkg": _JMS_TEMPLATE_PKG,
            })

            for record in result:
                source = record["source_code"] or ""
                destinations = set()
                has_send_calls = False

                for method_name in _SEND_METHODS:
                    # Match .convertAndSend("queue-name", ...)
                    pattern = rf'\.{method_name}\s*\(\s*"([^"]*)"'
                    matches = re.findall(pattern, source)
                    if matches:
                        has_send_calls = True
                        destinations.update(matches)
                    elif re.search(rf'\.{method_name}\s*\(', source):
                        has_send_calls = True

                if not has_send_calls:
                    continue

                self.log_info(
                    f"  JMS Producer: {record['name']} "
                    f"(destinations="
                    f"{list(destinations) or 'dynamic'})")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (c:Java:Class {
                            full_name: $full_name})
                        CREATE (p:Arch:JMSProducer {
                            name: $name
                        })
                        CREATE (p)-[:IMPLEMENTED_BY]->(c)
                    """, {
                        "full_name": record["full_name"],
                        "name": record["name"],
                    })

                    for dest in destinations:
                        if dest not in destinations_seen:
                            self._merge_destination(dest)
                            destinations_seen.add(dest)
                            stats["destinations"] += 1

                        run_cypher_write(self.driver, """
                            MATCH (p:Arch:JMSProducer)
                                  -[:IMPLEMENTED_BY]->
                                  (:Java:Class {
                                      full_name: $full_name})
                            MATCH (d:Arch:JMSDestination {
                                name: $destination})
                            CREATE (p)-[:SENDS_TO]->(d)
                        """, {
                            "full_name": record["full_name"],
                            "destination": dest,
                        })

                    stats["producers"] += 1
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create JMSProducer: {e}")

    def _merge_destination(self, name: str):
        run_cypher_write(self.driver, """
            MERGE (d:Arch:JMSDestination {name: $name})
        """, {"name": name})
