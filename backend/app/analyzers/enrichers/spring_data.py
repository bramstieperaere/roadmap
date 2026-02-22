import re

from app.analyzers.enrichers.base import TechnologyEnricher, parse_json
from app.neo4j_client import run_cypher_write

_REPO_SUPERTYPES: dict[str, str] = {
    "JpaRepository": "JPA",
    "CrudRepository": "CRUD",
    "PagingAndSortingRepository": "JPA",
    "MongoRepository": "Mongo",
    "ReactiveMongoRepository": "Mongo",
    "ReactiveCrudRepository": "Reactive",
    "ElasticsearchRepository": "Elasticsearch",
    "RedisRepository": "Redis",
}

_REPO_PATTERN = re.compile(
    r'(?:extends|implements)\s+(?:' +
    '|'.join(re.escape(k) for k in _REPO_SUPERTYPES) +
    r')\s*<\s*(\w+)')


class SpringDataEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        stats = {"repositories": 0}

        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                WHERE c.kind = 'interface'
                  AND c.supertypes IS NOT NULL
                RETURN c.full_name AS full_name,
                       c.name AS name,
                       c.supertypes AS supertypes,
                       c.source_code AS source_code
            """, {"module_name": self.module_name})

            for record in result:
                supertypes = parse_json(record["supertypes"])
                repo_type = None
                for st in supertypes:
                    if st in _REPO_SUPERTYPES:
                        repo_type = _REPO_SUPERTYPES[st]
                        break
                if not repo_type:
                    continue

                # Extract entity type from source code generics
                entity_type = ""
                source = record["source_code"] or ""
                match = _REPO_PATTERN.search(source)
                if match:
                    entity_type = match.group(1)

                self.log_info(
                    f"  Repository: {record['name']} "
                    f"({repo_type}, entity={entity_type or '?'})")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (c:Java:Class {full_name: $full_name})
                        CREATE (r:Arch:Repository {
                            name: $name,
                            entity_type: $entity_type,
                            repo_type: $repo_type
                        })
                        CREATE (r)-[:IMPLEMENTED_BY]->(c)
                    """, {
                        "full_name": record["full_name"],
                        "name": record["name"],
                        "entity_type": entity_type,
                        "repo_type": repo_type,
                    })
                    stats["repositories"] += 1
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create Repository: {e}")

        self.log_info(
            f"Spring Data: {stats['repositories']} repositories")
        return stats
