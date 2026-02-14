import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models_jobs import Job, JobLogEntry, JobStatus


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(self, repo_path: str, repo_index: int,
                   module_name: str, module_type: str) -> Job:
        job_id = str(uuid.uuid4())[:8]
        job = Job(
            id=job_id,
            repo_path=repo_path,
            repo_index=repo_index,
            module_name=module_name,
            module_type=module_type,
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(),
                      key=lambda j: j.created_at, reverse=True)

    def add_log(self, job_id: str, level: str, message: str):
        job = self._jobs.get(job_id)
        if job:
            job.log.append(JobLogEntry(
                timestamp=datetime.now(timezone.utc),
                level=level,
                message=message,
            ))

    def update_status(self, job_id: str, status: JobStatus,
                      error: str = None, summary: str = None):
        job = self._jobs.get(job_id)
        if job:
            job.status = status
            if status == JobStatus.RUNNING:
                job.started_at = datetime.now(timezone.utc)
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.completed_at = datetime.now(timezone.utc)
            if error:
                job.error = error
            if summary:
                job.summary = summary


job_store = JobStore()
