"""Commit processors: pluggable analyzers that extract structured data from commits."""

from abc import ABC, abstractmethod
from typing import Literal


class CommitProcessor(ABC):
    """Base class for commit processors.

    A commit processor detects relevant files in a commit and extracts
    structured data that gets stored on the Neo4j Commit node.
    """

    name: str          # unique identifier, e.g. "liquibase"
    label: str         # display name, e.g. "Liquibase DB Changes"
    description: str   # short description for the UI
    node_property: str  # Neo4j property name to store results, e.g. "db_changes"
    status: Literal["matured", "incubating"] = "matured"
    version: int = 1       # bump when processor logic changes

    @abstractmethod
    def detect(self, files_changed: list[str]) -> list[str]:
        """Return the subset of files_changed that this processor handles.

        Returns an empty list if the commit is not relevant.
        """

    @abstractmethod
    def process(self, repo_path: str, full_hash: str,
                matched_files: list[str],
                parent_full_hash: str | None = None) -> dict | None:
        """Process matched files from a commit.

        Args:
            repo_path: Filesystem path to the git repository.
            full_hash: Full commit hash (for git show).
            matched_files: Files returned by detect().
            parent_full_hash: Full hash of the parent commit (for diffing).

        Returns:
            Structured data dict to store on the commit node, or None.
        """


def get_all_processors() -> list[CommitProcessor]:
    """Return instances of all registered matured commit processors."""
    from .liquibase import LiquibaseProcessor
    from .jpa_entities import JpaEntityProcessor
    from .spring_endpoints import SpringEndpointProcessor
    from .spring_messaging import SpringMessagingProcessor
    from .spring_datasource import SpringDataSourceProcessor
    return [
        LiquibaseProcessor(),
        JpaEntityProcessor(),
        SpringEndpointProcessor(),
        SpringMessagingProcessor(),
        SpringDataSourceProcessor(),
    ]


def get_processors_by_name(names: list[str]) -> list[CommitProcessor]:
    """Return matured processor instances matching the given names."""
    all_procs = {p.name: p for p in get_all_processors()}
    return [all_procs[n] for n in names if n in all_procs]


def get_incubating_processors(config) -> list[CommitProcessor]:
    """Build incubating processor instances from config."""
    from .incubating import IncubatingProcessor
    processors = []
    for inc in config.incubating_processors:
        if not inc.name or not inc.file_patterns:
            continue
        processors.append(IncubatingProcessor(
            proc_name=inc.name,
            proc_label=inc.label or inc.name,
            proc_description=inc.description,
            instructions=inc.instructions,
            file_patterns=inc.file_patterns,
        ))
    return processors
