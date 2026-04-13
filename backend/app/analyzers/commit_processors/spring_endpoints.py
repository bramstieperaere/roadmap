"""Spring REST endpoint processor — extracts controller endpoints and paths."""

import re

from . import CommitProcessor
from .git_utils import git_show_file

_PATH_PATTERNS = [
    re.compile(r"\.java$", re.IGNORECASE),
]

# Controller detection
_CONTROLLER_RE = re.compile(r"@(?:Rest)?Controller\b")
_CLASS_RE = re.compile(r"\bclass\s+(\w+)")
_CLASS_MAPPING_RE = re.compile(
    r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\{]?([^"}\)]+)["\}]?'
)

# Endpoint annotations
_MAPPING_RE = re.compile(
    r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*"
    r"(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?\"([^\"]*)\""
    r"|(?:\(\s*\"([^\"]*)\")|\(|)"
)

# Method signature after mapping annotation
_METHOD_RE = re.compile(
    r"(?:public|protected|private)\s+\S+\s+(\w+)\s*\("
)

# Response status
_STATUS_RE = re.compile(r"@ResponseStatus\s*\(\s*(?:value\s*=\s*)?(\w+(?:\.\w+)?)")


def _parse_controller(content: str) -> dict | None:
    """Parse a Java file for Spring controller endpoints."""
    if not _CONTROLLER_RE.search(content):
        return None

    class_match = _CLASS_RE.search(content)
    if not class_match:
        return None

    class_name = class_match.group(1)

    # Extract class-level request mapping
    base_path = ""
    class_mapping = _CLASS_MAPPING_RE.search(content)
    if class_mapping:
        base_path = class_mapping.group(1).strip().rstrip("/")

    # Extract endpoints
    endpoints = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        mapping_match = _MAPPING_RE.search(line)
        if mapping_match:
            http_method = mapping_match.group(1).upper()
            if http_method == "REQUEST":
                http_method = "GET"  # default for @RequestMapping
            path = mapping_match.group(2) or mapping_match.group(3) or ""
            full_path = f"{base_path}/{path}".replace("//", "/") if path else base_path or "/"

            # Look for method name in next few lines
            method_name = "?"
            for j in range(i, min(i + 5, len(lines))):
                method_match = _METHOD_RE.search(lines[j])
                if method_match:
                    method_name = method_match.group(1)
                    break

            # Check for response status
            status = None
            for j in range(max(0, i - 2), min(i + 3, len(lines))):
                status_match = _STATUS_RE.search(lines[j])
                if status_match:
                    status = status_match.group(1)
                    break

            endpoint: dict = {
                "method": http_method,
                "path": full_path,
                "handler": method_name,
            }
            if status:
                endpoint["status"] = status

            endpoints.append(endpoint)
        i += 1

    if not endpoints:
        return None

    return {
        "controller": class_name,
        "basePath": base_path,
        "endpoints": endpoints,
    }


class SpringEndpointProcessor(CommitProcessor):
    name = "spring_endpoints"
    label = "Spring REST Endpoints"
    description = "Extracts REST controller endpoints, HTTP methods, and paths"
    node_property = "spring_endpoints"

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
        controllers = []
        parent_endpoints: set[str] = set()
        processed_files = []

        # Parse parent for diffing
        if parent_full_hash:
            for fp in matched_files:
                pc = git_show_file(repo_path, parent_full_hash, fp)
                if pc:
                    ctrl = _parse_controller(pc)
                    if ctrl:
                        for ep in ctrl["endpoints"]:
                            parent_endpoints.add(
                                f"{ep['method']} {ep['path']}")

        for file_path in matched_files:
            content = git_show_file(repo_path, full_hash, file_path)
            if not content:
                continue

            controller = _parse_controller(content)
            if controller:
                controller["file"] = file_path
                controllers.append(controller)
                processed_files.append(file_path)

        if not controllers:
            return None

        all_endpoints = []
        added_endpoints = []
        for c in controllers:
            for ep in c["endpoints"]:
                key = f"{ep['method']} {ep['path']}"
                all_endpoints.append(key)
                if parent_full_hash and key not in parent_endpoints:
                    added_endpoints.append(ep)

        result: dict = {
            "files": processed_files,
            "controllers": controllers,
            "endpoint_count": len(all_endpoints),
        }
        if parent_full_hash:
            removed = [k for k in parent_endpoints
                       if k not in set(all_endpoints)]
            result["diff"] = {
                "added_endpoints": added_endpoints,
                "removed_endpoints": removed,
            }
        return result
