import re

from app.analyzers.node_props import NodeMeta
from app.job_store import job_store
from app.neo4j_client import run_cypher_write

# Types to skip when extracting data model types
_SKIP_TYPES = {
    "void", "int", "long", "boolean", "float", "double", "byte",
    "char", "short",
    "String", "Integer", "Long", "Boolean", "Float", "Double", "Byte",
    "Character", "Short", "BigDecimal", "BigInteger", "Object", "Void",
    "Number",
    "List", "Set", "Map", "Collection", "Iterable", "Stream",
    "Iterator", "ArrayList", "HashMap", "HashSet", "LinkedList",
    "Optional", "CompletableFuture", "CompletionStage",
    "ResponseEntity", "HttpEntity",
    "Page", "Slice", "Mono", "Flux",
    "HttpServletRequest", "HttpServletResponse",
    "ServletRequest", "ServletResponse",
    "MultipartFile", "BindingResult", "Model", "ModelMap",
    "Principal", "Authentication",
    "URI", "URL", "Date", "LocalDate", "LocalDateTime", "Instant",
    "UUID", "Locale", "TimeZone", "ZonedDateTime", "OffsetDateTime",
}

_REPO_TYPE_TO_DB: dict[str, dict[str, str]] = {
    "JPA": {"technology": "sql", "name": "SQL Database"},
    "CRUD": {"technology": "sql", "name": "SQL Database"},
    "Mongo": {"technology": "mongo", "name": "MongoDB"},
    "Redis": {"technology": "redis", "name": "Redis"},
    "Elasticsearch": {
        "technology": "elasticsearch", "name": "Elasticsearch"},
    "Reactive": {"technology": "reactive", "name": "Reactive Database"},
}


def _extract_type_names(type_str: str) -> list[str]:
    """Extract meaningful type names from a Java type string.

    Strips generic wrappers and skips primitives/collections.
    E.g. 'ResponseEntity<List<OrderDto>>' -> ['OrderDto']
    """
    if not type_str:
        return []
    type_str = type_str.replace("[]", "").replace("...", "")
    names = re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", type_str)
    return [n for n in names if n not in _SKIP_TYPES]


def _extract_param_types(params_str: str) -> list[str]:
    """Extract type names from method parameter string.

    Parameters are stored as 'Type1 name1, Type2 name2'.
    """
    if not params_str:
        return []
    result = []
    for param in params_str.split(","):
        param = param.strip()
        if not param:
            continue
        parts = param.split()
        if parts:
            result.extend(_extract_type_names(parts[0]))
    return result


class DataFlowEnricher(NodeMeta):
    """Cross-module enricher that creates the Data domain view.

    Unlike TechnologyEnricher subclasses, this operates per-repository
    (cross-module), not per-module.
    """

    def __init__(self, job_id: str, driver, repo_name: str,
                 db_overrides: dict | None = None,
                 job_type: str = "data-flow"):
        self.job_id = job_id
        self.job_type = job_type
        self.driver = driver
        self.repo_name = repo_name
        self.db_overrides = db_overrides or {}

    def log_info(self, message: str):
        job_store.add_log(self.job_id, "info", message)

    def log_warn(self, message: str):
        job_store.add_log(self.job_id, "warn", message)

    def neo4j_session(self):
        from app.config import load_config_decrypted
        config = load_config_decrypted()
        return self.driver.session(database=config.neo4j.database)

    def enrich(self) -> dict:
        stats = {
            "services": 0, "endpoints": 0, "queues": 0,
            "databases": 0, "data_models": 0,
        }

        self.log_info(
            f"DataFlow enrichment starting for '{self.repo_name}'")

        self._clean()
        self._create_services(stats)
        self._create_inbound_endpoints(stats)
        self._create_outbound_endpoints(stats)
        self._create_queues(stats)
        self._create_databases(stats)
        self._create_data_models(stats)

        self.log_info(
            f"DataFlow: {stats['services']} services, "
            f"{stats['endpoints']} endpoints, "
            f"{stats['queues']} queues, "
            f"{stats['databases']} databases, "
            f"{stats['data_models']} data models")
        return stats

    # ---- Phase 1: Clean ----

    def _clean(self):
        """Delete existing Data nodes linked to this repo."""
        run_cypher_write(self.driver, """
            MATCH (ms:Arch:Microservice {name: $repo_name})
                  -[:IMPLEMENTED_BY]->(:Java:Repository)
            MATCH (ds:Data:Service)-[:MAPS_TO]->(ms)
            OPTIONAL MATCH (ds)-[]->(dc:Data)
            DETACH DELETE dc, ds
        """, {"repo_name": self.repo_name})
        run_cypher_write(self.driver, """
            MATCH (d:Data)
            WHERE NOT EXISTS { MATCH (d)-[]-() }
            DELETE d
        """)
        self.log_info("  Cleaned existing Data nodes")

    # ---- Phase 2: Services ----

    def _create_services(self, stats: dict):
        """Create Data:Service from Arch:Microservice."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                RETURN ms.name AS name, elementId(ms) AS ms_id
            """, {"repo_name": self.repo_name})

            for record in result:
                name = record["name"]
                try:
                    run_cypher_write(self.driver, """
                        MATCH (ms:Arch:Microservice)
                        WHERE elementId(ms) = $ms_id
                        CREATE (ds:Data:Service {
                            name: $name, is_external: false,
                            created_at: $created_at,
                            job_id: $job_id,
                            job_type: $job_type
                        })
                        CREATE (ds)-[:MAPS_TO]->(ms)
                    """, {
                        **self.node_meta(),
                        "ms_id": record["ms_id"],
                        "name": name,
                    })
                    stats["services"] += 1
                    self.log_info(f"  Service: {name}")
                except Exception as e:
                    self.log_warn(f"  Failed to create Service: {e}")

    # ---- Phase 3: Inbound endpoints ----

    def _create_inbound_endpoints(self, stats: dict):
        """Create Data:Endpoint(direction=inbound) from
        Arch:RESTEndpoint."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      <-[:IMPLEMENTED_BY]-(ri:Arch:RESTInterface)
                      -[:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint)
                RETURN ep.path AS path,
                       ep.http_method AS http_method,
                       elementId(ep) AS ep_id,
                       ms.name AS service_name
            """, {"repo_name": self.repo_name})

            for record in result:
                path = record["path"]
                http_method = record["http_method"]
                try:
                    run_cypher_write(self.driver, """
                        MATCH (ds:Data:Service {name: $service_name})
                        MATCH (ep:Arch:RESTEndpoint)
                        WHERE elementId(ep) = $ep_id
                        CREATE (de:Data:Endpoint {
                            path: $path,
                            http_method: $http_method,
                            direction: 'inbound',
                            service_name: $service_name,
                            created_at: $created_at,
                            job_id: $job_id,
                            job_type: $job_type
                        })
                        CREATE (ds)-[:EXPOSES]->(de)
                        CREATE (de)-[:MAPS_TO]->(ep)
                    """, {
                        **self.node_meta(),
                        "service_name": record["service_name"],
                        "ep_id": record["ep_id"],
                        "path": path,
                        "http_method": http_method,
                    })
                    stats["endpoints"] += 1
                    self.log_info(
                        f"  Inbound: {http_method} {path}")
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create inbound endpoint: {e}")

    # ---- Phase 4: Outbound endpoints ----

    def _create_outbound_endpoints(self, stats: dict):
        """Create Data:Endpoint(direction=outbound) from
        Arch:FeignEndpoint."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      <-[:IMPLEMENTED_BY]-(fc:Arch:FeignClient)
                      -[:HAS_ENDPOINT]->(fe:Arch:FeignEndpoint)
                RETURN fe.path AS path,
                       fe.http_method AS http_method,
                       elementId(fe) AS fe_id,
                       ms.name AS service_name
            """, {"repo_name": self.repo_name})

            for record in result:
                path = record["path"]
                http_method = record["http_method"]
                try:
                    run_cypher_write(self.driver, """
                        MATCH (ds:Data:Service {name: $service_name})
                        MATCH (fe:Arch:FeignEndpoint)
                        WHERE elementId(fe) = $fe_id
                        CREATE (de:Data:Endpoint {
                            path: $path,
                            http_method: $http_method,
                            direction: 'outbound',
                            service_name: $service_name,
                            created_at: $created_at,
                            job_id: $job_id,
                            job_type: $job_type
                        })
                        CREATE (ds)-[:CALLS]->(de)
                        CREATE (de)-[:MAPS_TO]->(fe)
                    """, {
                        **self.node_meta(),
                        "service_name": record["service_name"],
                        "fe_id": record["fe_id"],
                        "path": path,
                        "http_method": http_method,
                    })
                    stats["endpoints"] += 1
                    self.log_info(
                        f"  Outbound: {http_method} {path}")
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create outbound endpoint: {e}")

    # ---- Phase 5: Queues ----

    def _create_queues(self, stats: dict):
        """Create Data:Queue from Arch:JMSDestination."""
        queues_seen: dict[str, str] = {}   # name -> dest element_id
        consumer_queues: set[str] = set()
        producer_queues: set[str] = set()

        with self.neo4j_session() as session:
            # Destinations via listeners (consumers)
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(:Java:Class)
                      -[:HAS_METHOD]->(m:Java:Method)
                      <-[:IMPLEMENTED_BY]-(l:Arch:JMSListener)
                      -[:LISTENS_ON]->(d:Arch:JMSDestination)
                RETURN DISTINCT d.name AS name,
                       elementId(d) AS dest_id
            """, {"repo_name": self.repo_name})

            for record in result:
                name = record["name"]
                queues_seen[name] = record["dest_id"]
                consumer_queues.add(name)

            # Destinations via producers
            result2 = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      <-[:IMPLEMENTED_BY]-(p:Arch:JMSProducer)
                      -[:SENDS_TO]->(d:Arch:JMSDestination)
                RETURN DISTINCT d.name AS name,
                       elementId(d) AS dest_id
            """, {"repo_name": self.repo_name})

            for record in result2:
                name = record["name"]
                if name not in queues_seen:
                    queues_seen[name] = record["dest_id"]
                producer_queues.add(name)

        # Create queue nodes and relationships
        for name, dest_id in queues_seen.items():
            try:
                queue_type = (
                    "topic" if "topic" in name.lower() else "queue")
                run_cypher_write(self.driver, """
                    MATCH (d:Arch:JMSDestination)
                    WHERE elementId(d) = $dest_id
                    CREATE (dq:Data:Queue {
                        name: $name, type: $queue_type,
                        created_at: $created_at,
                        job_id: $job_id,
                        job_type: $job_type
                    })
                    CREATE (dq)-[:MAPS_TO]->(d)
                """, {
                    **self.node_meta(),
                    "dest_id": dest_id,
                    "name": name,
                    "queue_type": queue_type,
                })

                if name in producer_queues:
                    run_cypher_write(self.driver, """
                        MATCH (ds:Data:Service {name: $svc})
                        MATCH (dq:Data:Queue {name: $q})
                        CREATE (ds)-[:PRODUCES]->(dq)
                    """, {"svc": self.repo_name, "q": name})

                if name in consumer_queues:
                    run_cypher_write(self.driver, """
                        MATCH (ds:Data:Service {name: $svc})
                        MATCH (dq:Data:Queue {name: $q})
                        CREATE (ds)-[:CONSUMES]->(dq)
                    """, {"svc": self.repo_name, "q": name})

                stats["queues"] += 1
                roles = []
                if name in producer_queues:
                    roles.append("produces")
                if name in consumer_queues:
                    roles.append("consumes")
                self.log_info(
                    f"  Queue: {name} ({', '.join(roles)})")
            except Exception as e:
                self.log_warn(f"  Failed to create Queue: {e}")

    # ---- Phase 6: Databases ----

    def _create_databases(self, stats: dict):
        """Create Data:Database from Arch:Repository grouped by
        repo_type."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      <-[:IMPLEMENTED_BY]-(r:Arch:Repository)
                RETURN DISTINCT r.repo_type AS repo_type,
                       collect(DISTINCT elementId(r)) AS repo_ids
            """, {"repo_name": self.repo_name})

            for record in result:
                repo_type = record["repo_type"]
                repo_ids = record["repo_ids"]

                if repo_type in self.db_overrides:
                    override = self.db_overrides[repo_type]
                    db_name = override.get("name", repo_type)
                    technology = override.get(
                        "technology", repo_type.lower())
                else:
                    defaults = _REPO_TYPE_TO_DB.get(repo_type, {
                        "technology": repo_type.lower(),
                        "name": f"{repo_type} Database",
                    })
                    db_name = defaults["name"]
                    technology = defaults["technology"]

                try:
                    run_cypher_write(self.driver, """
                        MATCH (r:Arch:Repository)
                        WHERE elementId(r) = $repo_id
                        CREATE (db:Data:Database {
                            name: $name, technology: $technology,
                            created_at: $created_at,
                            job_id: $job_id,
                            job_type: $job_type
                        })
                        CREATE (db)-[:MAPS_TO]->(r)
                    """, {
                        **self.node_meta(),
                        "repo_id": repo_ids[0],
                        "name": db_name,
                        "technology": technology,
                    })

                    for rid in repo_ids[1:]:
                        run_cypher_write(self.driver, """
                            MATCH (db:Data:Database {name: $name})
                            MATCH (r:Arch:Repository)
                            WHERE elementId(r) = $repo_id
                            CREATE (db)-[:MAPS_TO]->(r)
                        """, {"name": db_name, "repo_id": rid})

                    run_cypher_write(self.driver, """
                        MATCH (ds:Data:Service {name: $svc})
                        MATCH (db:Data:Database {name: $db_name})
                        CREATE (ds)-[:READS_FROM]->(db)
                        CREATE (ds)-[:WRITES_TO]->(db)
                    """, {
                        "svc": self.repo_name,
                        "db_name": db_name,
                    })

                    stats["databases"] += 1
                    self.log_info(
                        f"  Database: {db_name} ({technology}, "
                        f"{len(repo_ids)} repos)")
                except Exception as e:
                    self.log_warn(
                        f"  Failed to create Database: {e}")

    # ---- Phase 7: Data Models ----

    def _create_data_models(self, stats: dict):
        """Create Data:DataModel from types used in
        endpoints/queues/repos."""
        # Build class index for fast FQN resolution
        self._all_fqns: set[str] = set()
        self._name_to_fqn: dict[str, str | None] = {}
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (c:Java:Class)
                RETURN c.full_name AS fqn, c.name AS name
            """)
            name_counts: dict[str, list[str]] = {}
            for record in result:
                fqn = record["fqn"]
                name = record["name"]
                self._all_fqns.add(fqn)
                name_counts.setdefault(name, []).append(fqn)
            # Only use unambiguous names for fallback resolution
            self._name_to_fqn = {
                name: fqns[0]
                for name, fqns in name_counts.items()
                if len(fqns) == 1
            }

        models: dict[str, dict] = {}  # fqn -> {name, kind, links}

        self._collect_endpoint_models(models)
        self._collect_jms_models(models)
        self._collect_entity_models(models)

        if not models:
            self.log_info("  No data models found")
            return

        for fqn, info in models.items():
            try:
                run_cypher_write(self.driver, """
                    MATCH (c:Java:Class {full_name: $fqn})
                    CREATE (dm:Data:DataModel {
                        full_name: $fqn,
                        name: $name,
                        kind: $kind,
                        created_at: $created_at,
                        job_id: $job_id,
                        job_type: $job_type
                    })
                    CREATE (dm)-[:MAPS_TO]->(c)
                """, {
                    **self.node_meta(),
                    "fqn": fqn,
                    "name": info["name"],
                    "kind": info["kind"],
                })

                for link in info["links"]:
                    run_cypher_write(self.driver, f"""
                        MATCH (dm:Data:DataModel {{full_name: $fqn}})
                        MATCH (n:Data)
                        WHERE elementId(n) = $target_id
                        CREATE (n)-[:{link['rel']}]->(dm)
                    """, {
                        "fqn": fqn,
                        "target_id": link["target_id"],
                    })

                stats["data_models"] += 1
                self.log_info(
                    f"  DataModel: {info['name']} ({info['kind']})")
            except Exception as e:
                self.log_warn(
                    f"  Failed to create DataModel {fqn}: {e}")

    def _collect_endpoint_models(self, models: dict):
        """Collect data models from REST and Feign endpoint methods."""
        with self.neo4j_session() as session:
            # Inbound REST endpoints
            result = session.run("""
                MATCH (ds:Data:Service {name: $repo_name})
                      -[:EXPOSES]->(de:Data:Endpoint)
                      -[:MAPS_TO]->(ep:Arch:RESTEndpoint)
                      -[:IMPLEMENTED_BY]->(m:Java:Method)
                MATCH (c:Java:Class)-[:HAS_METHOD]->(m)
                RETURN m.return_type AS return_type,
                       m.parameters AS parameters,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       c.full_name AS class_fqn,
                       elementId(de) AS endpoint_id
            """, {"repo_name": self.repo_name})

            self._process_endpoint_records(result, models)

            # Outbound Feign endpoints
            result2 = session.run("""
                MATCH (ds:Data:Service {name: $repo_name})
                      -[:CALLS]->(de:Data:Endpoint)
                      -[:MAPS_TO]->(fe:Arch:FeignEndpoint)
                      -[:IMPLEMENTED_BY]->(m:Java:Method)
                MATCH (c:Java:Class)-[:HAS_METHOD]->(m)
                RETURN m.return_type AS return_type,
                       m.parameters AS parameters,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       c.full_name AS class_fqn,
                       elementId(de) AS endpoint_id
            """, {"repo_name": self.repo_name})

            self._process_endpoint_records(result2, models)

    def _process_endpoint_records(self, result, models: dict):
        """Process endpoint query results into data models."""
        for record in result:
            endpoint_id = record["endpoint_id"]
            imports = record["imports"] or []
            star_imports = record["star_imports"] or []
            class_fqn = record["class_fqn"]

            # Return types -> response
            for type_name in _extract_type_names(
                    record["return_type"]):
                fqn = self._resolve_fqn(
                    type_name, imports, star_imports, class_fqn)
                if fqn:
                    self._add_model(
                        models, fqn, type_name, "response",
                        "RETURNS", endpoint_id)

            # Parameter types -> request
            for type_name in _extract_param_types(
                    record["parameters"]):
                fqn = self._resolve_fqn(
                    type_name, imports, star_imports, class_fqn)
                if fqn:
                    self._add_model(
                        models, fqn, type_name, "request",
                        "ACCEPTS", endpoint_id)

    def _collect_jms_models(self, models: dict):
        """Collect data models from JMS listener method parameters."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ds:Data:Service {name: $repo_name})
                      -[:CONSUMES]->(dq:Data:Queue)
                      -[:MAPS_TO]->(d:Arch:JMSDestination)
                      <-[:LISTENS_ON]-(l:Arch:JMSListener)
                      -[:IMPLEMENTED_BY]->(m:Java:Method)
                MATCH (c:Java:Class)-[:HAS_METHOD]->(m)
                RETURN m.parameters AS parameters,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       c.full_name AS class_fqn,
                       elementId(dq) AS queue_id
            """, {"repo_name": self.repo_name})

            for record in result:
                queue_id = record["queue_id"]
                imports = record["imports"] or []
                star_imports = record["star_imports"] or []

                for type_name in _extract_param_types(
                        record["parameters"]):
                    fqn = self._resolve_fqn(
                        type_name, imports, star_imports,
                        record["class_fqn"])
                    if fqn:
                        self._add_model(
                            models, fqn, type_name, "message",
                            "CARRIES", queue_id)

    def _collect_entity_models(self, models: dict):
        """Collect entity types from Spring Data repositories."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (ms:Arch:Microservice {name: $repo_name})
                      -[:IMPLEMENTED_BY]->(:Java:Repository)
                      -[:CONTAINS_MODULE]->(:Java:Module)
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                      <-[:IMPLEMENTED_BY]-(r:Arch:Repository)
                RETURN r.entity_type AS entity_type,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       c.full_name AS class_fqn
            """, {"repo_name": self.repo_name})

            for record in result:
                entity_type = record["entity_type"]
                if not entity_type:
                    continue
                imports = record["imports"] or []
                star_imports = record["star_imports"] or []

                fqn = self._resolve_fqn(
                    entity_type, imports, star_imports,
                    record["class_fqn"])
                if fqn:
                    self._add_model(
                        models, fqn, entity_type, "entity",
                        None, None)

    def _add_model(self, models: dict, fqn: str, name: str,
                   kind: str, rel: str | None,
                   target_id: str | None):
        """Add or update a data model entry."""
        if fqn not in models:
            models[fqn] = {"name": name, "kind": kind, "links": []}
        # Entity kind takes priority
        if kind == "entity":
            models[fqn]["kind"] = "entity"
        if rel and target_id:
            link = {"rel": rel, "target_id": target_id}
            if link not in models[fqn]["links"]:
                models[fqn]["links"].append(link)

    def _resolve_fqn(self, simple_name: str, imports: list[str],
                     star_imports: list[str],
                     source_class_fqn: str) -> str | None:
        """Resolve a simple type name to FQN using imports.

        Returns None if the class doesn't exist in the graph.
        """
        # Explicit imports
        for imp in imports:
            if imp.endswith(f".{simple_name}"):
                if imp in self._all_fqns:
                    return imp

        # Star imports
        for pkg in star_imports:
            fqn = f"{pkg}.{simple_name}"
            if fqn in self._all_fqns:
                return fqn

        # Same package
        if "." in source_class_fqn:
            pkg = source_class_fqn.rsplit(".", 1)[0]
            fqn = f"{pkg}.{simple_name}"
            if fqn in self._all_fqns:
                return fqn

        # Fallback: unambiguous simple name match
        return self._name_to_fqn.get(simple_name)
