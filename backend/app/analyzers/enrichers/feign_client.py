from app.analyzers.enrichers.base import (
    TechnologyEnricher, parse_json, get_annotation,
)
from app.neo4j_client import run_cypher_write

_FEIGN_CLIENT_FQN = "org.springframework.cloud.openfeign.FeignClient"

_PKG = "org.springframework.web.bind.annotation"

# Mapping from annotation FQN to HTTP method
_METHOD_ANNOTATIONS = {
    f"{_PKG}.GetMapping": "GET",
    f"{_PKG}.PostMapping": "POST",
    f"{_PKG}.PutMapping": "PUT",
    f"{_PKG}.DeleteMapping": "DELETE",
    f"{_PKG}.PatchMapping": "PATCH",
}

_REQUEST_MAPPING_FQN = f"{_PKG}.RequestMapping"

_REQUEST_METHODS = {
    "RequestMethod.GET": "GET", "RequestMethod.POST": "POST",
    "RequestMethod.PUT": "PUT", "RequestMethod.DELETE": "DELETE",
    "RequestMethod.PATCH": "PATCH",
    "GET": "GET", "POST": "POST", "PUT": "PUT",
    "DELETE": "DELETE", "PATCH": "PATCH",
}


def _get_path(ann: dict | None) -> str:
    if not ann or not ann.get("arguments"):
        return ""
    args = ann["arguments"]
    val = args.get("value") or args.get("path") or ""
    if isinstance(val, list):
        return val[0] if val else ""
    return val


def _join_paths(base: str, method_path: str) -> str:
    base = base.rstrip("/") if base else ""
    method_path = method_path.lstrip("/") if method_path else ""
    if not base and not method_path:
        return "/"
    if not method_path:
        return base or "/"
    if not base:
        return "/" + method_path
    return base + "/" + method_path


class FeignClientEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        stats = {"clients": 0, "endpoints": 0}

        clients = self._load_feign_clients()
        if not clients:
            self.log_info("Feign: no clients found")
            return stats

        for cls in clients:
            ann = get_annotation(cls["annotations"], _FEIGN_CLIENT_FQN)
            if not ann:
                continue
            args = ann.get("arguments") or {}

            client_name = (args.get("name") or args.get("value")
                           or cls["name"])
            url = args.get("url", "")
            path = args.get("path", "")
            service_id = args.get("name") or args.get("value") or ""

            # Normalize list values
            if isinstance(client_name, list):
                client_name = client_name[0] if client_name else cls["name"]
            if isinstance(url, list):
                url = url[0] if url else ""
            if isinstance(path, list):
                path = path[0] if path else ""
            if isinstance(service_id, list):
                service_id = service_id[0] if service_id else ""

            self.log_info(
                f"  FeignClient: {cls['name']} "
                f"(service={service_id!r})")

            try:
                run_cypher_write(self.driver, """
                    MATCH (c:Java:Class {full_name: $full_name})
                    CREATE (fc:Arch:FeignClient {
                        name: $name,
                        url: $url,
                        path: $path,
                        service_id: $service_id
                    })
                    CREATE (fc)-[:IMPLEMENTED_BY]->(c)
                """, {
                    "full_name": cls["full_name"],
                    "name": client_name,
                    "url": url,
                    "path": path,
                    "service_id": service_id,
                })
                stats["clients"] += 1
            except Exception as e:
                self.log_warn(
                    f"  Failed to create FeignClient: {e}")
                continue

            # Process methods for endpoints
            base_path = path
            for method in cls["methods"]:
                endpoint_info = self._resolve_endpoint(
                    method["annotations"], base_path)
                if not endpoint_info:
                    continue

                self.log_info(
                    f"    Endpoint: {endpoint_info['http_method']} "
                    f"{endpoint_info['path']} -> "
                    f"{method['full_name']}")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (fc:Arch:FeignClient)
                              -[:IMPLEMENTED_BY]->
                              (:Java:Class {full_name: $class_fn})
                        MATCH (m:Java:Method {
                            full_name: $method_fn})
                        CREATE (fe:Arch:FeignEndpoint {
                            path: $path,
                            http_method: $http_method
                        })
                        CREATE (fc)-[:HAS_ENDPOINT]->(fe)
                        CREATE (fe)-[:IMPLEMENTED_BY]->(m)
                    """, {
                        "class_fn": cls["full_name"],
                        "method_fn": method["full_name"],
                        "path": endpoint_info["path"],
                        "http_method": endpoint_info["http_method"],
                    })
                    stats["endpoints"] += 1
                except Exception as e:
                    self.log_warn(
                        f"    Failed to create FeignEndpoint: {e}")

        self.log_info(
            f"Feign: {stats['clients']} clients, "
            f"{stats['endpoints']} endpoints")
        return stats

    def _load_feign_clients(self) -> list[dict]:
        """Load classes with @FeignClient annotation."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Java:Method)
                RETURN c.full_name AS full_name,
                       c.name AS name,
                       c.annotations AS class_annotations,
                       collect({
                           full_name: m.full_name,
                           name: m.name,
                           annotations: m.annotations
                       }) AS methods
            """, {"module_name": self.module_name})

            clients = []
            for record in result:
                class_annotations = parse_json(
                    record["class_annotations"])
                if not get_annotation(
                        class_annotations, _FEIGN_CLIENT_FQN):
                    continue
                methods = []
                for m in record["methods"]:
                    if m["name"] is None:
                        continue
                    methods.append({
                        "full_name": m["full_name"],
                        "name": m["name"],
                        "annotations": parse_json(m["annotations"]),
                    })
                clients.append({
                    "full_name": record["full_name"],
                    "name": record["name"],
                    "annotations": class_annotations,
                    "methods": methods,
                })
            return clients

    def _resolve_endpoint(self, annotations: list[dict],
                          base_path: str) -> dict | None:
        # Check specific mapping annotations
        for ann_fqn, http_method in _METHOD_ANNOTATIONS.items():
            ann = get_annotation(annotations, ann_fqn)
            if ann:
                path = _join_paths(base_path, _get_path(ann))
                return {"path": path, "http_method": http_method}

        # Check generic @RequestMapping
        ann = get_annotation(annotations, _REQUEST_MAPPING_FQN)
        if ann:
            path = _join_paths(base_path, _get_path(ann))
            http_method = "GET"
            if ann.get("arguments"):
                method_val = ann["arguments"].get("method")
                if isinstance(method_val, list):
                    method_val = method_val[0] if method_val else None
                if method_val and method_val in _REQUEST_METHODS:
                    http_method = _REQUEST_METHODS[method_val]
            return {"path": path, "http_method": http_method}

        return None
