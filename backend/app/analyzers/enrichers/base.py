import json
from abc import ABC, abstractmethod

from app.analyzers.node_props import NodeMeta
from app.job_store import job_store


def parse_json(val: str | None) -> list:
    """Parse a JSON string into a list, returning [] on failure."""
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def get_annotation(annotations: list[dict], *fqns: str) -> dict | None:
    """Find first annotation matching any of the given FQNs."""
    for ann in annotations:
        if ann.get("name") in fqns:
            return ann
    return None


class TechnologyEnricher(ABC, NodeMeta):
    def __init__(self, job_id: str, driver, module_name: str,
                 job_type: str = "enrichment"):
        self.job_id = job_id
        self.job_type = job_type
        self.driver = driver
        self.module_name = module_name

    def log_info(self, message: str):
        job_store.add_log(self.job_id, "info", message)

    def log_warn(self, message: str):
        job_store.add_log(self.job_id, "warn", message)

    def neo4j_session(self):
        from app.config import load_config_decrypted
        config = load_config_decrypted()
        return self.driver.session(database=config.neo4j.database)

    @abstractmethod
    def enrich(self, all_classes: list[dict]) -> dict:
        """Process parsed classes and write enrichment to Neo4j.
        Returns a stats dict (e.g. {"endpoints": 5})."""
        pass
