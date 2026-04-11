"""Liquibase commit processor — detects and parses Liquibase changelog XML files."""

import re
import subprocess
import xml.etree.ElementTree as ET

from . import CommitProcessor

_LB_NS = "http://www.liquibase.org/xml/ns/dbchangelog"

# File path patterns that suggest a Liquibase changelog
_PATH_PATTERNS = [
    re.compile(r"(^|/)db/changelog/.*\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)db/migration/.*\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)liquibase/.*\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)changelog[^/]*\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)changeset[^/]*\.xml$", re.IGNORECASE),
]


def _find_all(parent: ET.Element, local: str) -> list[ET.Element]:
    """Find child elements with or without namespace."""
    found = parent.findall(f"{{{_LB_NS}}}{local}")
    if not found:
        found = parent.findall(local)
    return found


def _parse_columns(parent: ET.Element) -> list[dict]:
    cols = []
    for col in _find_all(parent, "column"):
        entry: dict[str, str] = {"name": col.get("name", "?")}
        col_type = col.get("type")
        if col_type:
            entry["type"] = col_type
        cols.append(entry)
    return cols


def _parse_changeset(cs: ET.Element) -> list[dict]:
    """Extract database change operations from a single changeSet element."""
    changes = []

    for child in cs:
        # Strip namespace from tag
        tag = child.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        if tag == "createTable":
            changes.append({
                "op": "createTable",
                "table": child.get("tableName", "?"),
                "columns": _parse_columns(child),
            })

        elif tag == "dropTable":
            changes.append({
                "op": "dropTable",
                "table": child.get("tableName", "?"),
            })

        elif tag == "renameTable":
            changes.append({
                "op": "renameTable",
                "oldTable": child.get("oldTableName", "?"),
                "newTable": child.get("newTableName", "?"),
            })

        elif tag == "addColumn":
            changes.append({
                "op": "addColumn",
                "table": child.get("tableName", "?"),
                "columns": _parse_columns(child),
            })

        elif tag == "dropColumn":
            changes.append({
                "op": "dropColumn",
                "table": child.get("tableName", "?"),
                "column": child.get("columnName", "?"),
            })

        elif tag == "renameColumn":
            changes.append({
                "op": "renameColumn",
                "table": child.get("tableName", "?"),
                "oldName": child.get("oldColumnName", "?"),
                "newName": child.get("newColumnName", "?"),
            })

        elif tag == "modifyDataType":
            changes.append({
                "op": "modifyDataType",
                "table": child.get("tableName", "?"),
                "column": child.get("columnName", "?"),
                "newType": child.get("newDataType", "?"),
            })

        elif tag == "addPrimaryKey":
            changes.append({
                "op": "addPrimaryKey",
                "table": child.get("tableName", "?"),
                "columns": child.get("columnNames", "?"),
            })

        elif tag == "addForeignKeyConstraint":
            changes.append({
                "op": "addForeignKeyConstraint",
                "baseTable": child.get("baseTableName", "?"),
                "baseColumn": child.get("baseColumnNames", "?"),
                "refTable": child.get("referencedTableName", "?"),
                "refColumn": child.get("referencedColumnNames", "?"),
            })

        elif tag == "createIndex":
            changes.append({
                "op": "createIndex",
                "table": child.get("tableName", "?"),
                "index": child.get("indexName", "?"),
                "columns": _parse_columns(child),
            })

        elif tag == "dropIndex":
            changes.append({
                "op": "dropIndex",
                "index": child.get("indexName", "?"),
            })

        elif tag == "addUniqueConstraint":
            changes.append({
                "op": "addUniqueConstraint",
                "table": child.get("tableName", "?"),
                "columns": child.get("columnNames", "?"),
            })

        elif tag == "addNotNullConstraint":
            changes.append({
                "op": "addNotNullConstraint",
                "table": child.get("tableName", "?"),
                "column": child.get("columnName", "?"),
            })

        elif tag == "dropNotNullConstraint":
            changes.append({
                "op": "dropNotNullConstraint",
                "table": child.get("tableName", "?"),
                "column": child.get("columnName", "?"),
            })

        elif tag == "sql":
            sql_text = (child.text or "").strip()
            if sql_text:
                changes.append({
                    "op": "sql",
                    "sql": sql_text[:200],
                })

        elif tag == "createSequence":
            changes.append({
                "op": "createSequence",
                "sequence": child.get("sequenceName", "?"),
            })

        elif tag == "dropSequence":
            changes.append({
                "op": "dropSequence",
                "sequence": child.get("sequenceName", "?"),
            })

    return changes


def _git_show_file(repo_path: str, full_hash: str, file_path: str) -> str | None:
    """Retrieve file content at a specific commit using git show."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{full_hash}:{file_path}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except Exception:
        return None


class LiquibaseProcessor(CommitProcessor):
    name = "liquibase"
    label = "Liquibase DB Changes"
    description = "Detects Liquibase changelog XML files and extracts database schema changes"
    node_property = "db_changes"

    def detect(self, files_changed: list[str]) -> list[str]:
        matched = []
        for f in files_changed:
            for pat in _PATH_PATTERNS:
                if pat.search(f):
                    matched.append(f)
                    break
        return matched

    def process(self, repo_path: str, full_hash: str,
                matched_files: list[str]) -> dict | None:
        all_changes = []
        processed_files = []

        for file_path in matched_files:
            content = _git_show_file(repo_path, full_hash, file_path)
            if not content:
                continue

            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue

            # Verify this is a Liquibase changelog
            root_tag = root.tag
            if "}" in root_tag:
                root_tag = root_tag.split("}", 1)[1]
            if root_tag != "databaseChangeLog":
                continue

            file_changes = []
            for cs in _find_all(root, "changeSet"):
                file_changes.extend(_parse_changeset(cs))

            if file_changes:
                processed_files.append(file_path)
                all_changes.extend(file_changes)

        if not all_changes:
            return None

        return {
            "files": processed_files,
            "changes": all_changes,
        }
