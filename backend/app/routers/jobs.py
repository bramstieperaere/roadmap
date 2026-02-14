import asyncio

from fastapi import APIRouter, HTTPException

from app.config import load_config_decrypted
from app.job_store import job_store
from app.models_jobs import (
    StartJobRequest, StartJobResponse,
    JobListResponse, JobSummary, JobDetailResponse, JobStatus,
)
from app.neo4j_client import get_neo4j_driver
from app.session import session

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


async def _run_job(job_id: str, repo_path: str,
                   module_name: str, module_type: str,
                   relative_path: str):
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()
        try:
            if module_type == "java":
                from app.analyzers.java_maven import JavaMavenAnalyzer
                analyzer = JavaMavenAnalyzer(job_id)
            else:
                job_store.add_log(job_id, "error",
                                  f"Unsupported module type: {module_type}")
                job_store.update_status(job_id, JobStatus.FAILED,
                                        error=f"Unsupported module type: {module_type}")
                return

            summary = await asyncio.to_thread(
                analyzer.run, repo_path, module_name,
                relative_path, driver)

            job_store.update_status(job_id, JobStatus.COMPLETED,
                                    summary=summary)
        finally:
            driver.close()
    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


@router.post("/start", response_model=StartJobResponse)
async def start_job(request: StartJobRequest):
    _require_unlocked()
    config = load_config_decrypted()

    if request.repo_index < 0 or request.repo_index >= len(config.repositories):
        raise HTTPException(status_code=400, detail="Invalid repository index")

    repo = config.repositories[request.repo_index]

    if request.module_index < 0 or request.module_index >= len(repo.modules):
        raise HTTPException(status_code=400, detail="Invalid module index")

    module = repo.modules[request.module_index]

    job = job_store.create_job(
        repo_path=repo.path,
        repo_index=request.repo_index,
        module_name=module.name,
        module_type=module.type,
    )

    asyncio.create_task(_run_job(
        job.id, repo.path, module.name,
        module.type, module.relative_path))

    return StartJobResponse(
        job_id=job.id,
        message=f"Job started for module '{module.name}'")


@router.get("", response_model=JobListResponse)
def list_jobs():
    _require_unlocked()
    jobs = job_store.get_all_jobs()
    summaries = [
        JobSummary(
            id=j.id,
            repo_path=j.repo_path,
            module_name=j.module_name,
            module_type=j.module_type,
            status=j.status,
            created_at=j.created_at,
            completed_at=j.completed_at,
            summary=j.summary,
            error=j.error,
        )
        for j in jobs
    ]
    return JobListResponse(jobs=summaries)


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: str):
    _require_unlocked()
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse(job=job)
