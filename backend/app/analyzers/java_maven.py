import os
from pathlib import Path
from typing import Optional

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node

from app.analyzers.base import BaseAnalyzer
from app.neo4j_client import run_cypher_write

JAVA_LANGUAGE = Language(tsjava.language())

_TYPE_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
}

_MODIFIER_KEYWORDS = frozenset({
    "public", "private", "protected", "static", "abstract",
    "final", "synchronized", "native", "transient", "volatile",
    "strictfp", "default", "sealed",
})


class JavaMavenAnalyzer(BaseAnalyzer):

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self._parser = Parser(JAVA_LANGUAGE)

    def run(self, repo_path: str, module_name: str,
            relative_path: str, neo4j_driver) -> str:
        module_path = Path(repo_path) / relative_path
        self.log_info(f"Starting analysis of module '{module_name}' "
                      f"at {module_path}")

        # Phase 1: Clear existing data for this module
        self._clear_module_data(neo4j_driver, module_name)

        # Phase 2: Create Module node
        self._create_module_node(neo4j_driver, module_name,
                                 relative_path, repo_path)

        # Phase 3: Walk directory for .java files
        java_files = self._walk_java_files(module_path)
        self.log_info(f"Found {len(java_files)} .java files")

        # Phase 4: Parse each file
        all_classes = []
        packages_seen = set()
        stats = {"packages": 0, "classes": 0, "methods": 0,
                 "parse_errors": 0}

        for i, java_file in enumerate(java_files, 1):
            rel = java_file.relative_to(module_path)
            try:
                result = self._parse_java_file(java_file, module_path)
                if not result or not result["classes"]:
                    self.log_warn(f"No types found in {rel}")
                    continue

                pkg = result["package"]
                if pkg and pkg not in packages_seen:
                    packages_seen.add(pkg)
                    self._create_package_node(
                        neo4j_driver, module_name, pkg)
                    stats["packages"] += 1

                file_classes = 0
                file_methods = 0
                for cls in result["classes"]:
                    self._create_class_node(neo4j_driver, cls)
                    stats["classes"] += 1
                    file_classes += 1
                    all_classes.append(cls)

                    for method in cls["methods"]:
                        self._create_method_node(
                            neo4j_driver, cls["full_name"], method)
                        stats["methods"] += 1
                        file_methods += 1

                self.log_info(
                    f"[{i}/{len(java_files)}] {rel}: "
                    f"{file_classes} types, {file_methods} methods")

            except Exception as e:
                stats["parse_errors"] += 1
                self.log_warn(
                    f"[{i}/{len(java_files)}] Failed {rel}: "
                    f"{type(e).__name__}: {e}")

        # Phase 5: Cross-reference imports
        self.log_info("Cross-referencing imports...")
        import_count = self._create_import_relationships(
            neo4j_driver, all_classes)
        self.log_info(f"Created {import_count} IMPORTS relationships")

        summary = (f"{stats['packages']} packages, "
                   f"{stats['classes']} classes, "
                   f"{stats['methods']} methods, "
                   f"{import_count} imports")
        if stats["parse_errors"] > 0:
            summary += f", {stats['parse_errors']} parse errors"

        self.log_info(f"Analysis complete: {summary}")
        return summary

    # ----- Level 1: Directory Walk -----

    def _walk_java_files(self, module_path: Path) -> list[Path]:
        java_files = []
        source_roots = [
            module_path / "src" / "main" / "java",
            module_path / "src" / "test" / "java",
        ]
        for source_root in source_roots:
            if not source_root.is_dir():
                self.log_info(
                    f"Source root not found: "
                    f"{source_root.relative_to(module_path)}")
                continue
            self.log_info(
                f"Walking {source_root.relative_to(module_path)}")
            for dirpath, _, filenames in os.walk(source_root):
                for fn in sorted(filenames):
                    if fn.endswith(".java"):
                        java_files.append(Path(dirpath) / fn)
        return java_files

    # ----- Level 2: Java AST Parsing (tree-sitter) -----

    def _parse_java_file(self, file_path: Path,
                         module_path: Path) -> Optional[dict]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        root = tree.root_node

        if root.has_error:
            rel = file_path.relative_to(module_path)
            self.log_warn(f"Partial parse (syntax errors): {rel}")

        package_name = self._extract_package(root)
        imports, star_imports = self._extract_imports(root)

        rel_path = file_path.relative_to(module_path)
        is_test = str(rel_path).replace("\\", "/").startswith("src/test/")

        classes = []
        type_nodes = [c for c in root.children
                      if c.type in _TYPE_DECLARATIONS]
        self._extract_types_from_nodes(
            type_nodes, package_name, is_test,
            str(rel_path), imports, star_imports,
            classes, parent_name=None)

        return {"package": package_name, "classes": classes}

    def _extract_package(self, root: Node) -> str:
        for child in root.children:
            if child.type == "package_declaration":
                for c in child.children:
                    if c.type in ("scoped_identifier", "identifier"):
                        return c.text.decode("utf-8")
        return ""

    def _extract_imports(self, root: Node):
        imports = []
        star_imports = []
        for child in root.children:
            if child.type == "import_declaration":
                text = child.text.decode("utf-8").strip()
                text = (text.removeprefix("import ")
                        .removesuffix(";").strip())
                if text.startswith("static "):
                    text = text.removeprefix("static ").strip()
                if text.endswith(".*"):
                    star_imports.append(text.removesuffix(".*"))
                else:
                    imports.append(text)
        return imports, star_imports

    def _extract_types_from_nodes(self, nodes, package_name: str,
                                  is_test: bool, file_path: str,
                                  imports: list, star_imports: list,
                                  classes: list,
                                  parent_name: Optional[str]):
        for node in nodes:
            kind = _TYPE_DECLARATIONS.get(node.type)
            if not kind:
                continue

            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            simple_name = name_node.text.decode("utf-8")

            if parent_name:
                full_name = f"{parent_name}.{simple_name}"
            elif package_name:
                full_name = f"{package_name}.{simple_name}"
            else:
                full_name = simple_name

            modifiers = self._get_modifiers(node)
            is_abstract = "abstract" in modifiers
            visibility = self._visibility_from_modifiers(modifiers)
            methods = self._extract_methods(node)

            cls_info = {
                "name": simple_name,
                "full_name": full_name,
                "package": package_name,
                "kind": kind,
                "is_abstract": is_abstract,
                "is_test": is_test,
                "file_path": file_path,
                "visibility": visibility,
                "imports": imports,
                "star_imports": star_imports,
                "methods": methods,
            }
            classes.append(cls_info)

            # Recurse into body for inner classes
            body = node.child_by_field_name("body")
            if body:
                inner_types = [c for c in body.children
                               if c.type in _TYPE_DECLARATIONS]
                if inner_types:
                    self._extract_types_from_nodes(
                        inner_types, package_name, is_test,
                        file_path, imports, star_imports,
                        classes, parent_name=full_name)

    def _get_modifiers(self, node: Node) -> set[str]:
        modifiers = set()
        for child in node.children:
            if child.type == "modifiers":
                for mod_child in child.children:
                    text = mod_child.text.decode("utf-8")
                    if text in _MODIFIER_KEYWORDS:
                        modifiers.add(text)
                break
        return modifiers

    def _visibility_from_modifiers(self, modifiers: set[str]) -> str:
        if "public" in modifiers:
            return "public"
        if "protected" in modifiers:
            return "protected"
        if "private" in modifiers:
            return "private"
        return "package-private"

    def _extract_methods(self, type_node: Node) -> list[dict]:
        methods = []
        body = type_node.child_by_field_name("body")
        if not body:
            return methods

        for child in body.children:
            if child.type == "method_declaration":
                name_node = child.child_by_field_name("name")
                name = (name_node.text.decode("utf-8")
                        if name_node else "?")

                type_n = child.child_by_field_name("type")
                return_type = (type_n.text.decode("utf-8")
                               if type_n else "void")

                params = self._format_params(child)
                mods = self._get_modifiers(child)

                methods.append({
                    "name": name,
                    "return_type": return_type,
                    "parameters": params,
                    "is_static": "static" in mods,
                    "is_abstract": "abstract" in mods,
                    "visibility": self._visibility_from_modifiers(mods),
                })
            elif child.type == "constructor_declaration":
                params = self._format_params(child)
                mods = self._get_modifiers(child)

                methods.append({
                    "name": "<init>",
                    "return_type": "void",
                    "parameters": params,
                    "is_static": False,
                    "is_abstract": False,
                    "visibility": self._visibility_from_modifiers(mods),
                })
        return methods

    def _format_params(self, method_node: Node) -> str:
        params_node = method_node.child_by_field_name("parameters")
        if not params_node:
            return ""
        parts = []
        for child in params_node.children:
            if child.type in ("formal_parameter", "spread_parameter"):
                type_node = child.child_by_field_name("type")
                name_node = child.child_by_field_name("name")
                if type_node and name_node:
                    type_str = type_node.text.decode("utf-8")
                    name_str = name_node.text.decode("utf-8")
                    if child.type == "spread_parameter":
                        type_str += "..."
                    dims = child.child_by_field_name("dimensions")
                    if dims:
                        type_str += dims.text.decode("utf-8")
                    parts.append(f"{type_str} {name_str}")
        return ", ".join(parts)

    # ----- Neo4j Operations -----

    def _clear_module_data(self, driver, module_name: str):
        self.log_info(f"Clearing existing data for module '{module_name}'")
        run_cypher_write(driver, """
            MATCH (m:Module {name: $name})
            OPTIONAL MATCH (m)-[*]->(n)
            DETACH DELETE n, m
        """, {"name": module_name})

    def _create_module_node(self, driver, module_name: str,
                            relative_path: str, repo_path: str):
        run_cypher_write(driver, """
            MERGE (m:Module {name: $name})
            SET m.path = $path, m.repository = $repo
        """, {"name": module_name, "path": relative_path,
              "repo": repo_path})

    def _create_package_node(self, driver, module_name: str,
                             package_name: str):
        short_name = (package_name.rsplit(".", 1)[-1]
                      if "." in package_name else package_name)
        run_cypher_write(driver, """
            MATCH (m:Module {name: $module_name})
            MERGE (p:Package {full_name: $full_name})
            SET p.name = $name
            MERGE (m)-[:CONTAINS_PACKAGE]->(p)
        """, {"module_name": module_name,
              "full_name": package_name,
              "name": short_name})

    def _create_class_node(self, driver, cls: dict):
        run_cypher_write(driver, """
            MATCH (p:Package {full_name: $package})
            MERGE (c:Class {full_name: $full_name})
            SET c.name = $name,
                c.kind = $kind,
                c.is_abstract = $is_abstract,
                c.is_test = $is_test,
                c.file_path = $file_path,
                c.visibility = $visibility
            MERGE (p)-[:CONTAINS_CLASS]->(c)
        """, {
            "package": cls["package"],
            "full_name": cls["full_name"],
            "name": cls["name"],
            "kind": cls["kind"],
            "is_abstract": cls["is_abstract"],
            "is_test": cls["is_test"],
            "file_path": cls["file_path"],
            "visibility": cls["visibility"],
        })

    def _create_method_node(self, driver, class_full_name: str,
                            method: dict):
        run_cypher_write(driver, """
            MATCH (c:Class {full_name: $class_name})
            CREATE (m:Method {
                name: $name,
                return_type: $return_type,
                parameters: $parameters,
                is_static: $is_static,
                is_abstract: $is_abstract,
                visibility: $visibility
            })
            CREATE (c)-[:HAS_METHOD]->(m)
        """, {
            "class_name": class_full_name,
            "name": method["name"],
            "return_type": method["return_type"],
            "parameters": method["parameters"],
            "is_static": method["is_static"],
            "is_abstract": method["is_abstract"],
            "visibility": method["visibility"],
        })

    def _create_import_relationships(self, driver,
                                     all_classes: list[dict]) -> int:
        known_classes = {cls["full_name"] for cls in all_classes}
        count = 0

        for cls in all_classes:
            targets = set()

            for imp in cls["imports"]:
                if imp in known_classes:
                    targets.add(imp)

            for star_pkg in cls["star_imports"]:
                for known in known_classes:
                    pkg_of_known = (known.rsplit(".", 1)[0]
                                    if "." in known else "")
                    if pkg_of_known == star_pkg:
                        targets.add(known)

            targets.discard(cls["full_name"])

            if targets:
                run_cypher_write(driver, """
                    MATCH (source:Class {full_name: $source})
                    UNWIND $targets AS target_name
                    MATCH (target:Class {full_name: target_name})
                    MERGE (source)-[:IMPORTS]->(target)
                """, {"source": cls["full_name"],
                      "targets": list(targets)})
                count += len(targets)

        return count
