from app.analyzers.enrichers.base import TechnologyEnricher, parse_json
from app.neo4j_client import run_cypher_write

_SCHEDULED_FQN = "org.springframework.scheduling.annotation.Scheduled"


class SpringScheduledEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        stats = {"scheduled_tasks": 0}

        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      -[:HAS_METHOD]->(m:Java:Method)
                RETURN c.name AS class_name,
                       m.full_name AS method_full_name,
                       m.name AS method_name,
                       m.annotations AS method_annotations
            """, {"module_name": self.module_name})

            for record in result:
                annotations = parse_json(record["method_annotations"])
                sched_ann = None
                for ann in annotations:
                    if ann.get("name") == _SCHEDULED_FQN:
                        sched_ann = ann
                        break
                if not sched_ann:
                    continue

                args = sched_ann.get("arguments") or {}

                self.log_info(
                    f"  Scheduled: {record['class_name']}."
                    f"{record['method_name']}")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (m:Java:Method {
                            full_name: $method_full_name})
                        CREATE (st:Arch:ScheduledTask {
                            cron: $cron,
                            fixed_delay: $fixed_delay,
                            fixed_rate: $fixed_rate,
                            initial_delay: $initial_delay,
                            zone: $zone
                        })
                        CREATE (st)-[:IMPLEMENTED_BY]->(m)
                    """, {
                        "method_full_name": record["method_full_name"],
                        "cron": str(args.get("cron", "")),
                        "fixed_delay": str(args.get("fixedDelay", "")),
                        "fixed_rate": str(args.get("fixedRate", "")),
                        "initial_delay": str(args.get("initialDelay", "")),
                        "zone": str(args.get("zone", "")),
                    })
                    stats["scheduled_tasks"] += 1
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create ScheduledTask: {e}")

        self.log_info(
            f"Spring Scheduled: {stats['scheduled_tasks']} tasks")
        return stats
