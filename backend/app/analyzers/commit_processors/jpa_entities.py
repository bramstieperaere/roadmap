"""JPA Entity processor — extracts entity classes, table mappings, and relationships."""

import re

from . import CommitProcessor
from .git_utils import git_show_file

# Match Java files likely to contain JPA entities
_PATH_PATTERNS = [
    re.compile(r"\.java$", re.IGNORECASE),
]

# Annotation patterns
_ENTITY_RE = re.compile(r"@Entity\b")
_TABLE_RE = re.compile(r'@Table\s*\(\s*name\s*=\s*"([^"]+)"')
_CLASS_RE = re.compile(r"\bclass\s+(\w+)")
_MAPPED_SUPERCLASS_RE = re.compile(r"@MappedSuperclass\b")
_EMBEDDABLE_RE = re.compile(r"@Embeddable\b")

# Field annotations
_COLUMN_RE = re.compile(r'@Column\s*\([^)]*name\s*=\s*"([^"]+)"[^)]*\)')
_ID_RE = re.compile(r"@Id\b")
_GENERATED_RE = re.compile(r"@GeneratedValue\b")
_ONE_TO_MANY_RE = re.compile(r"@OneToMany\b")
_MANY_TO_ONE_RE = re.compile(r"@ManyToOne\b")
_ONE_TO_ONE_RE = re.compile(r"@OneToOne\b")
_MANY_TO_MANY_RE = re.compile(r"@ManyToMany\b")
_JOIN_COL_RE = re.compile(r'@JoinColumn\s*\([^)]*name\s*=\s*"([^"]+)"')
_ENUM_RE = re.compile(r"@Enumerated\b")
_FIELD_RE = re.compile(
    r"(?:private|protected|public)\s+"
    r"(?:(?:final|static|transient)\s+)*"
    r"([\w<>,\s\?]+?)\s+(\w+)\s*[;=]"
)


def _parse_entity(content: str) -> dict | None:
    """Parse a Java file for JPA entity information."""
    if not _ENTITY_RE.search(content) and not _MAPPED_SUPERCLASS_RE.search(content):
        if not _EMBEDDABLE_RE.search(content):
            return None

    class_match = _CLASS_RE.search(content)
    if not class_match:
        return None

    class_name = class_match.group(1)
    table_match = _TABLE_RE.search(content)
    table_name = table_match.group(1) if table_match else class_name.lower()

    kind = "entity"
    if _MAPPED_SUPERCLASS_RE.search(content):
        kind = "mapped_superclass"
    elif _EMBEDDABLE_RE.search(content):
        kind = "embeddable"

    # Extract fields with their annotations
    fields = []
    lines = content.split("\n")
    annotation_buffer: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@"):
            annotation_buffer.append(stripped)
            continue

        field_match = _FIELD_RE.search(stripped)
        if field_match:
            field_type = field_match.group(1).strip()
            field_name = field_match.group(2)
            annotations_text = " ".join(annotation_buffer)

            field_info: dict = {"name": field_name, "type": field_type}

            # Check for column mapping
            col_match = _COLUMN_RE.search(annotations_text)
            if col_match:
                field_info["column"] = col_match.group(1)

            # Check for ID
            if _ID_RE.search(annotations_text):
                field_info["id"] = True
            if _GENERATED_RE.search(annotations_text):
                field_info["generated"] = True

            # Check relationships
            if _ONE_TO_MANY_RE.search(annotations_text):
                field_info["relation"] = "OneToMany"
            elif _MANY_TO_ONE_RE.search(annotations_text):
                field_info["relation"] = "ManyToOne"
                join_match = _JOIN_COL_RE.search(annotations_text)
                if join_match:
                    field_info["join_column"] = join_match.group(1)
            elif _ONE_TO_ONE_RE.search(annotations_text):
                field_info["relation"] = "OneToOne"
            elif _MANY_TO_MANY_RE.search(annotations_text):
                field_info["relation"] = "ManyToMany"

            if _ENUM_RE.search(annotations_text):
                field_info["enumerated"] = True

            fields.append(field_info)

        if not stripped.startswith("@"):
            annotation_buffer = []

    # Deduplicate fields by name (Builder pattern creates duplicates)
    seen_names: set[str] = set()
    unique_fields = []
    for f in fields:
        if f["name"] not in seen_names:
            seen_names.add(f["name"])
            unique_fields.append(f)

    return {
        "class": class_name,
        "table": table_name,
        "kind": kind,
        "fields": unique_fields,
    }


def _compute_diff(current_entities: list[dict],
                   parent_by_class: dict[str, dict]) -> dict:
    """Compute the delta between current and parent entity state."""
    added_entities = []
    modified_entities = []

    for entity in current_entities:
        cls = entity["class"]
        parent = parent_by_class.get(cls)
        if not parent:
            # Entirely new entity
            added_entities.append({
                "class": cls,
                "table": entity.get("table"),
                "fields": entity.get("fields", []),
            })
        else:
            # Compare fields
            parent_fields = {f["name"]: f for f in parent.get("fields", [])}
            current_fields = {f["name"]: f for f in entity.get("fields", [])}

            added_fields = [
                f for name, f in current_fields.items()
                if name not in parent_fields]
            removed_fields = [
                f for name, f in parent_fields.items()
                if name not in current_fields]
            changed_fields = []
            for name, f in current_fields.items():
                if name in parent_fields and f != parent_fields[name]:
                    changed_fields.append(f)

            if added_fields or removed_fields or changed_fields:
                modified_entities.append({
                    "class": cls,
                    "table": entity.get("table"),
                    "added_fields": added_fields,
                    "removed_fields": removed_fields,
                    "changed_fields": changed_fields,
                })

    # Removed entities (in parent but not in current)
    current_classes = {e["class"] for e in current_entities}
    removed_entities = [
        {"class": cls, "table": p.get("table")}
        for cls, p in parent_by_class.items()
        if cls not in current_classes
    ]

    return {
        "added_entities": added_entities,
        "removed_entities": removed_entities,
        "modified_entities": modified_entities,
    }


class JpaEntityProcessor(CommitProcessor):
    name = "jpa_entities"
    label = "JPA Entities"
    description = "Extracts JPA entity classes, table mappings, columns, and relationships"
    node_property = "jpa_entities"

    def detect(self, files_changed: list[str]) -> list[str]:
        matched = []
        for f in files_changed:
            for pat in _PATH_PATTERNS:
                if pat.search(f):
                    matched.append(f)
                    break
        return matched

    def process(self, repo_path: str, full_hash: str,
                matched_files: list[str],
                parent_full_hash: str | None = None) -> dict | None:
        entities = []
        parent_entities_by_class: dict[str, dict] = {}
        processed_files = []

        # Parse parent versions for diffing
        if parent_full_hash:
            for file_path in matched_files:
                parent_content = git_show_file(
                    repo_path, parent_full_hash, file_path)
                if parent_content:
                    pe = _parse_entity(parent_content)
                    if pe:
                        parent_entities_by_class[pe["class"]] = pe

        for file_path in matched_files:
            content = git_show_file(repo_path, full_hash, file_path)
            if not content:
                continue

            entity = _parse_entity(content)
            if entity:
                entity["file"] = file_path
                entities.append(entity)
                processed_files.append(file_path)

        if not entities:
            return None

        # Compute diff
        diff = _compute_diff(entities, parent_entities_by_class)

        return {
            "files": processed_files,
            "entities": entities,
            "diff": diff,
        }
