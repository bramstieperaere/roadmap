from app.analyzers.enrichers.base import (
    TechnologyEnricher, parse_json, get_annotation,
)
from app.neo4j_client import run_cypher_write

_PKG = "org.springframework.web.bind.annotation"

# Mapping from FQN annotation to HTTP method
_METHOD_ANNOTATIONS = {
    f"{_PKG}.GetMapping": "GET",
    f"{_PKG}.PostMapping": "POST",
    f"{_PKG}.PutMapping": "PUT",
    f"{_PKG}.DeleteMapping": "DELETE",
    f"{_PKG}.PatchMapping": "PATCH",
}

# RequestMethod enum values
_REQUEST_METHODS = {
    "RequestMethod.GET": "GET",
    "RequestMethod.POST": "POST",
    "RequestMethod.PUT": "PUT",
    "RequestMethod.DELETE": "DELETE",
    "RequestMethod.PATCH": "PATCH",
    "RequestMethod.HEAD": "HEAD",
    "RequestMethod.OPTIONS": "OPTIONS",
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "DELETE": "DELETE",
    "PATCH": "PATCH",
}

_ALL_MAPPING_FQNS = frozenset(
    list(_METHOD_ANNOTATIONS.keys()) + [f"{_PKG}.RequestMapping"])

_CONTROLLER_ANNOTATIONS = frozenset({
    f"{_PKG}.RestController",
    f"{_PKG}.Controller",
})

_REQUEST_MAPPING_FQN = f"{_PKG}.RequestMapping"


def _has_annotation(annotations: list[dict], *fqns: str) -> bool:
    return get_annotation(annotations, *fqns) is not None


def _has_any_mapping(annotations: list[dict]) -> bool:
    return any(ann.get("name") in _ALL_MAPPING_FQNS for ann in annotations)


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


def _resolve_http_method(ann: dict) -> str | None:
    if not ann.get("arguments"):
        return None
    args = ann["arguments"]
    method_val = args.get("method")
    if not method_val:
        return None
    if isinstance(method_val, list):
        method_val = method_val[0] if method_val else None
    if method_val and method_val in _REQUEST_METHODS:
        return _REQUEST_METHODS[method_val]
    return None


class SpringWebEnricher(TechnologyEnricher):

    def enrich(self, all_classes: list[dict]) -> dict:
        """Enrich from Neo4j graph data. all_classes is ignored —
        we load everything from Neo4j for cross-module support."""
        return self.enrich_from_graph()

    def enrich_from_graph(self) -> dict:
        """Load classes from Neo4j and create architecture nodes."""
        stats = {"controllers": 0, "endpoints": 0}

        classes = self._load_module_controllers()
        if not classes:
            self.log_info("Spring Web: no controllers found")
            return stats

        for cls in classes:
            stats["controllers"] += 1

            # Class-level @RequestMapping base path
            class_annotations = cls["annotations"]
            class_mapping = get_annotation(class_annotations,
                                            _REQUEST_MAPPING_FQN)
            if not class_mapping:
                # Check interfaces for class-level @RequestMapping
                for iface in self._resolve_supertypes_from_graph(cls):
                    iface_mapping = get_annotation(
                        iface["annotations"], _REQUEST_MAPPING_FQN)
                    if iface_mapping:
                        class_mapping = iface_mapping
                        break
            base_path = _get_path(class_mapping)

            self.log_info(
                f"  Controller: {cls['name']} "
                f"(full_name={cls['full_name']}, "
                f"base_path={base_path!r})")

            # Create RESTInterface
            try:
                run_cypher_write(self.driver, """
                    MATCH (c:Java:Class {full_name: $full_name})
                    CREATE (ri:Arch:RESTInterface {
                        name: $name,
                        base_path: $base_path
                    })
                    CREATE (ri)-[:IMPLEMENTED_BY]->(c)
                """, {
                    "full_name": cls["full_name"],
                    "name": cls["name"],
                    "base_path": base_path,
                })
            except Exception as e:
                self.log_warn(
                    f"  Failed to create RESTInterface for "
                    f"{cls['name']}: {e}")
                continue

            # Build interface method annotations for inheritance
            iface_methods = self._build_iface_method_map(cls)

            # Process methods
            for method in cls["methods"]:
                method_annotations = method["annotations"]
                endpoint_info = self._resolve_endpoint(
                    method_annotations, base_path)

                # Inherited from interface
                if not endpoint_info and method["name"] in iface_methods:
                    endpoint_info = self._resolve_endpoint(
                        iface_methods[method["name"]], base_path)

                if not endpoint_info:
                    continue

                method_full_name = method["full_name"]

                self.log_info(
                    f"    Endpoint: {endpoint_info['http_method']} "
                    f"{endpoint_info['path']} -> {method_full_name}")

                try:
                    run_cypher_write(self.driver, """
                        MATCH (ri:Arch:RESTInterface {name: $ri_name})
                              -[:IMPLEMENTED_BY]->
                              (:Java:Class {full_name: $class_full_name})
                        MATCH (m:Java:Method {full_name: $method_full_name})
                        CREATE (ep:Arch:RESTEndpoint {
                            path: $path,
                            http_method: $http_method,
                            produces: $produces,
                            consumes: $consumes
                        })
                        CREATE (ri)-[:HAS_ENDPOINT]->(ep)
                        CREATE (ep)-[:IMPLEMENTED_BY]->(m)
                    """, {
                        "ri_name": cls["name"],
                        "class_full_name": cls["full_name"],
                        "method_full_name": method_full_name,
                        "path": endpoint_info["path"],
                        "http_method": endpoint_info["http_method"],
                        "produces": endpoint_info.get("produces", ""),
                        "consumes": endpoint_info.get("consumes", ""),
                    })
                except Exception as e:
                    self.log_warn(
                        f"    Failed to create RESTEndpoint: {e}")
                    continue
                stats["endpoints"] += 1

        self.log_info(
            f"Spring Web: {stats['controllers']} controllers, "
            f"{stats['endpoints']} endpoints")
        return stats

    # ----- Neo4j data loading -----

    def _load_module_controllers(self) -> list[dict]:
        """Load all classes in this module that have controller annotations."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (:Java:Module {name: $module_name})
                      -[:CONTAINS_PACKAGE]->(:Java:Package)
                      -[:CONTAINS_CLASS]->(c:Java:Class)
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Java:Method)
                RETURN c.full_name AS full_name,
                       c.name AS name,
                       c.annotations AS class_annotations,
                       c.supertypes AS supertypes,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       collect({
                           full_name: m.full_name,
                           name: m.name,
                           annotations: m.annotations
                       }) AS methods
            """, {"module_name": self.module_name})

            controllers = []
            for record in result:
                class_annotations = parse_json(record["class_annotations"])
                if not _has_annotation(class_annotations,
                                       *_CONTROLLER_ANNOTATIONS):
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
                controllers.append({
                    "full_name": record["full_name"],
                    "name": record["name"],
                    "annotations": class_annotations,
                    "supertypes": parse_json(record["supertypes"]),
                    "imports": record["imports"] or [],
                    "star_imports": record["star_imports"] or [],
                    "methods": methods,
                })
            return controllers

    def _load_class_from_graph(self, full_name: str) -> dict | None:
        """Load a single class with its method annotations from Neo4j."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (c:Java:Class {full_name: $full_name})
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Java:Method)
                RETURN c.full_name AS full_name,
                       c.name AS name,
                       c.annotations AS class_annotations,
                       c.supertypes AS supertypes,
                       c.imports AS imports,
                       c.star_imports AS star_imports,
                       collect({
                           full_name: m.full_name,
                           name: m.name,
                           annotations: m.annotations
                       }) AS methods
            """, {"full_name": full_name})
            record = result.single()
            if not record or not record["full_name"]:
                return None
            methods = []
            for m in record["methods"]:
                if m["name"] is None:
                    continue
                methods.append({
                    "full_name": m["full_name"],
                    "name": m["name"],
                    "annotations": parse_json(m["annotations"]),
                })
            return {
                "full_name": record["full_name"],
                "name": record["name"],
                "annotations": parse_json(record["class_annotations"]),
                "supertypes": parse_json(record["supertypes"]),
                "imports": record["imports"] or [],
                "star_imports": record["star_imports"] or [],
                "methods": methods,
            }

    def _resolve_supertypes_from_graph(self, cls: dict) -> list[dict]:
        """Resolve supertypes using imports stored on the class node."""
        supertypes = cls.get("supertypes", [])
        if not supertypes:
            return []

        # Build import map from class attributes
        import_map = {}
        for imp in cls.get("imports", []):
            simple = imp.rsplit(".", 1)[-1]
            import_map[simple] = imp

        full_name = cls["full_name"]
        package = (full_name.rsplit(".", 1)[0]
                   if "." in full_name else "")

        resolved = []
        for st in supertypes:
            iface = None
            # Try explicit imports
            fqn = import_map.get(st)
            if fqn:
                iface = self._load_class_from_graph(fqn)
            # Try star imports
            if not iface:
                for star_pkg in cls.get("star_imports", []):
                    fqn = f"{star_pkg}.{st}"
                    iface = self._load_class_from_graph(fqn)
                    if iface:
                        break
            # Try same package
            if not iface and package:
                fqn = f"{package}.{st}"
                iface = self._load_class_from_graph(fqn)
            # Fallback: search by simple name across all modules
            if not iface:
                iface = self._find_class_by_name(st)
            if iface:
                resolved.append(iface)
            else:
                self.log_info(
                    f"    Supertype '{st}' not found in graph")
        return resolved

    def _find_class_by_name(self, simple_name: str) -> dict | None:
        """Find a class by simple name across all modules."""
        with self.neo4j_session() as session:
            result = session.run("""
                MATCH (c:Java:Class {name: $name})
                RETURN c.full_name AS full_name
            """, {"name": simple_name})
            records = list(result)
            if len(records) == 1:
                return self._load_class_from_graph(
                    records[0]["full_name"])
        return None

    def _build_iface_method_map(self, cls: dict) -> dict[str, list[dict]]:
        """Build method_name -> annotations from interface methods."""
        iface_methods: dict[str, list[dict]] = {}
        for iface in self._resolve_supertypes_from_graph(cls):
            for method in iface.get("methods", []):
                annotations = method.get("annotations", [])
                if _has_any_mapping(annotations):
                    iface_methods[method["name"]] = annotations
        return iface_methods

    # ----- Endpoint resolution -----

    def _resolve_endpoint(self, annotations: list[dict],
                          base_path: str) -> dict | None:
        for ann_fqn, http_method in _METHOD_ANNOTATIONS.items():
            ann = get_annotation(annotations, ann_fqn)
            if ann:
                path = _join_paths(base_path, _get_path(ann))
                result = {"path": path, "http_method": http_method}
                self._add_media_types(ann, result)
                return result

        ann = get_annotation(annotations, _REQUEST_MAPPING_FQN)
        if ann:
            path = _join_paths(base_path, _get_path(ann))
            http_method = _resolve_http_method(ann) or "GET"
            result = {"path": path, "http_method": http_method}
            self._add_media_types(ann, result)
            return result

        return None

    def _add_media_types(self, ann: dict, result: dict):
        if not ann.get("arguments"):
            return
        args = ann["arguments"]
        for key in ("produces", "consumes"):
            val = args.get(key)
            if val:
                if isinstance(val, list):
                    result[key] = ", ".join(val)
                else:
                    result[key] = val
