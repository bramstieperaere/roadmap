"""Spring DataSource processor — extracts database connection configuration."""

import re

from . import CommitProcessor
from .git_utils import git_show_file

_PATH_PATTERNS = [
    re.compile(r"application[^/]*\.(properties|ya?ml)$", re.IGNORECASE),
    re.compile(r"persistence\.xml$", re.IGNORECASE),
    re.compile(r"DataSource.*\.java$", re.IGNORECASE),
    re.compile(r"DatabaseConfig.*\.java$", re.IGNORECASE),
]

# Properties patterns
_JDBC_URL_RE = re.compile(
    r"(?:spring\.datasource|jdbc)[\w.]*\.url\s*[=:]\s*(.+)")
_DRIVER_RE = re.compile(
    r"(?:spring\.datasource|jdbc)[\w.]*\.driver(?:-class-name)?\s*[=:]\s*(.+)")
_DIALECT_RE = re.compile(
    r"(?:spring\.jpa|hibernate)[\w.]*\.(?:dialect|database-platform)\s*[=:]\s*(.+)")
_DB_PLATFORM_RE = re.compile(
    r"spring\.jpa\.database\s*[=:]\s*(.+)")
_USERNAME_RE = re.compile(
    r"(?:spring\.datasource|jdbc)[\w.]*\.username\s*[=:]\s*(.+)")

# YAML patterns (indented key: value)
_YAML_URL_RE = re.compile(r"\s+url\s*:\s*(.+)")
_YAML_DRIVER_RE = re.compile(r"\s+driver-class-name\s*:\s*(.+)")
_YAML_DIALECT_RE = re.compile(r"\s+(?:dialect|database-platform)\s*:\s*(.+)")

# Java annotation patterns
_JAVA_URL_RE = re.compile(r'"(jdbc:\w+://[^"]+)"')
_JAVA_DRIVER_RE = re.compile(r'"((?:com|org)\.\w+(?:\.\w+)*Driver)"')

# Technology detection from JDBC URL
_DB_TECH_MAP = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "oracle": "Oracle",
    "sqlserver": "SQL Server",
    "mssql": "SQL Server",
    "h2": "H2",
    "hsqldb": "HSQLDB",
    "derby": "Derby",
    "mongodb": "MongoDB",
    "db2": "DB2",
}


def _detect_db_tech(url: str) -> str:
    """Detect database technology from JDBC URL."""
    url_lower = url.lower()
    for key, name in _DB_TECH_MAP.items():
        if key in url_lower:
            return name
    return "unknown"


def _parse_properties(content: str, file_path: str) -> list[dict]:
    """Parse .properties or .yml for datasource configuration."""
    datasources: list[dict] = []
    current: dict = {}

    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        for pattern in [_JDBC_URL_RE, _YAML_URL_RE]:
            m = pattern.search(line)
            if m:
                if current.get("url"):
                    datasources.append(current)
                    current = {}
                url = m.group(1).strip().strip('"').strip("'")
                current["url"] = url
                current["technology"] = _detect_db_tech(url)

        for pattern in [_DRIVER_RE, _YAML_DRIVER_RE]:
            m = pattern.search(line)
            if m:
                current["driver"] = m.group(1).strip().strip('"').strip("'")

        for pattern in [_DIALECT_RE, _YAML_DIALECT_RE]:
            m = pattern.search(line)
            if m:
                current["dialect"] = m.group(1).strip().strip('"').strip("'")

        m = _DB_PLATFORM_RE.search(line)
        if m:
            current["platform"] = m.group(1).strip()

        m = _USERNAME_RE.search(line)
        if m:
            current["username"] = m.group(1).strip()

    if current:
        datasources.append(current)

    return datasources


def _parse_java_config(content: str) -> list[dict]:
    """Parse Java @Configuration class for datasource beans."""
    datasources: list[dict] = []

    for m in _JAVA_URL_RE.finditer(content):
        url = m.group(1)
        datasources.append({
            "url": url,
            "technology": _detect_db_tech(url),
        })

    for m in _JAVA_DRIVER_RE.finditer(content):
        driver = m.group(1)
        if not datasources:
            datasources.append({})
        datasources[-1]["driver"] = driver

    return datasources


class SpringDataSourceProcessor(CommitProcessor):
    name = "spring_datasource"
    label = "Spring DataSource"
    description = "Extracts database connection configuration (JDBC URLs, drivers, dialects)"
    node_property = "spring_datasource"

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
        all_datasources: list[dict] = []
        processed_files = []

        for file_path in matched_files:
            content = git_show_file(repo_path, full_hash, file_path)
            if not content:
                continue

            if file_path.endswith(".java"):
                ds = _parse_java_config(content)
            else:
                ds = _parse_properties(content, file_path)

            if ds:
                for d in ds:
                    d["file"] = file_path
                all_datasources.extend(ds)
                processed_files.append(file_path)

        if not all_datasources:
            return None

        return {
            "files": processed_files,
            "datasources": all_datasources,
        }
