from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobLogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str


class Job(BaseModel):
    id: str
    repo_path: str
    repo_index: int
    module_name: str
    module_type: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    log: list[JobLogEntry] = []
    summary: Optional[str] = None
    error: Optional[str] = None


class StartJobRequest(BaseModel):
    repo_index: int
    module_index: int


class StartRepoRequest(BaseModel):
    repo_index: int


class StartJobResponse(BaseModel):
    job_id: str
    message: str


class StartRepoResponse(BaseModel):
    job_ids: list[str]
    message: str


class JobSummary(BaseModel):
    id: str
    repo_path: str
    module_name: str
    module_type: str
    status: JobStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    summary: Optional[str] = None
    error: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]


class JobDetailResponse(BaseModel):
    job: Job
