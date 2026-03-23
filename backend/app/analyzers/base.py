from abc import ABC, abstractmethod

from app.analyzers.node_props import NodeMeta
from app.job_store import job_store


class BaseAnalyzer(ABC, NodeMeta):
    def __init__(self, job_id: str, job_type: str):
        self.job_id = job_id
        self.job_type = job_type

    def log_info(self, message: str):
        job_store.add_log(self.job_id, "info", message)

    def log_warn(self, message: str):
        job_store.add_log(self.job_id, "warn", message)

    def log_error(self, message: str):
        job_store.add_log(self.job_id, "error", message)

    @abstractmethod
    def run(self, repo_path: str, module_name: str,
            relative_path: str, neo4j_driver) -> str:
        pass
