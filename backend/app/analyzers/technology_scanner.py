import json

from app.job_store import job_store
from app.neo4j_client import run_cypher_write

# Import prefix -> technology key
_IMPORT_SIGNALS: dict[str, str] = {
    "org.springframework.web.bind.annotation": "spring-web",
    "org.springframework.jms": "spring-jms",
    "org.springframework.scheduling.annotation": "spring-scheduled",
    "org.springframework.cloud.openfeign": "feign",
    "org.springframework.data": "spring-data",
    "org.springframework.web.client.RestTemplate": "rest-clients",
    "org.springframework.web.reactive.function.client": "rest-clients",
}

# Annotation FQN -> technology key
_ANNOTATION_SIGNALS: dict[str, str] = {
    "org.springframework.web.bind.annotation.RestController": "spring-web",
    "org.springframework.web.bind.annotation.Controller": "spring-web",
    "org.springframework.jms.annotation.JmsListener": "spring-jms",
    "org.springframework.scheduling.annotation.Scheduled": "spring-scheduled",
    "org.springframework.cloud.openfeign.FeignClient": "feign",
}

# Supertype simple name -> technology key
_SUPERTYPE_SIGNALS: dict[str, str] = {
    "JpaRepository": "spring-data",
    "CrudRepository": "spring-data",
    "PagingAndSortingRepository": "spring-data",
    "MongoRepository": "spring-data",
    "ReactiveMongoRepository": "spring-data",
    "ReactiveCrudRepository": "spring-data",
    "ElasticsearchRepository": "spring-data",
}


class TechnologyScanner:
    """Detects technologies used in a module by querying the Java metamodel
    already in Neo4j."""

    def __init__(self, job_id: str, driver, module_name: str):
        self.job_id = job_id
        self.driver = driver
        self.module_name = module_name

    def log_info(self, message: str):
        job_store.add_log(self.job_id, "info", message)

    def detect(self) -> list[str]:
        """Detect technologies and store on the Module node.
        Returns sorted list of technology keys."""
        detected: set[str] = set()
        detected |= self._scan_imports()
        detected |= self._scan_annotations()
        detected |= self._scan_supertypes()

        result = sorted(detected)
        self.log_info(
            f"Technology scan for {self.module_name}: {result or 'none'}")
        self._store_detected(result)
        return result

    def _neo4j_session(self):
        from app.config import load_config_decrypted
        config = load_config_decrypted()
        return self.driver.session(database=config.neo4j.database)

    def _scan_imports(self) -> set[str]:
        detected: set[str] = set()
        with self._neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                RETURN c.imports AS imports,
                       c.star_imports AS star_imports
            """, {"module_name": self.module_name})

            for record in result:
                for imp in (record["imports"] or []):
                    for prefix, tech in _IMPORT_SIGNALS.items():
                        if imp.startswith(prefix):
                            detected.add(tech)
                for star in (record["star_imports"] or []):
                    for prefix, tech in _IMPORT_SIGNALS.items():
                        if prefix.startswith(star + ".") or \
                                star.startswith(prefix):
                            detected.add(tech)
        return detected

    def _scan_annotations(self) -> set[str]:
        detected: set[str] = set()
        with self._neo4j_session() as session:
            # Scan class and method annotations
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Java:Method)
                RETURN c.annotations AS class_ann,
                       collect(m.annotations) AS method_anns
            """, {"module_name": self.module_name})

            for record in result:
                all_ann_strings = [record["class_ann"]]
                all_ann_strings.extend(record["method_anns"])
                for ann_str in all_ann_strings:
                    if not ann_str:
                        continue
                    try:
                        annotations = json.loads(ann_str)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    for ann in annotations:
                        name = ann.get("name", "")
                        if name in _ANNOTATION_SIGNALS:
                            detected.add(_ANNOTATION_SIGNALS[name])
        return detected

    def _scan_supertypes(self) -> set[str]:
        detected: set[str] = set()
        with self._neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                WHERE c.supertypes IS NOT NULL
                RETURN c.supertypes AS supertypes
            """, {"module_name": self.module_name})

            for record in result:
                try:
                    supertypes = json.loads(record["supertypes"])
                except (json.JSONDecodeError, TypeError):
                    continue
                for st in supertypes:
                    if st in _SUPERTYPE_SIGNALS:
                        detected.add(_SUPERTYPE_SIGNALS[st])
        return detected

    def _store_detected(self, technologies: list[str]):
        run_cypher_write(self.driver, """
            MATCH (m:Java:Module {name: $name})
            SET m.detected_technologies = $techs
        """, {"name": self.module_name, "techs": technologies})
