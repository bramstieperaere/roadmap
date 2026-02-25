"""Router for functional documentation processing and serving."""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import has_encrypted_fields, CONFIG_PATH
from app.job_store import job_store
from app.models_jobs import JobStatus
from app.session import session

router = APIRouter(prefix="/api/functional", tags=["functional"])


class ProcessRequest(BaseModel):
    space_key: str
    page_ids: list[str] | None = None


class ProcessResponse(BaseModel):
    job_id: str
    message: str


def _require_unlocked():
    if has_encrypted_fields() and not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked.")


def _output_root():
    return CONFIG_PATH.parent / "poc" / "functional-desc-structured"


@router.post("/process", response_model=ProcessResponse)
async def process_pages(request: ProcessRequest):
    """Start an AI processing job for Confluence pages."""
    _require_unlocked()

    label = request.space_key
    if request.page_ids:
        label += f" ({len(request.page_ids)} pages)"

    job = job_store.create_job(
        repo_path=f"confluence/{request.space_key}",
        repo_index=-1,
        module_name=label,
        module_type="functional_doc",
    )

    asyncio.create_task(_run_job(job.id, request.space_key, request.page_ids))
    return ProcessResponse(job_id=job.id, message=f"Processing started for {label}")


async def _run_job(job_id: str, space_key: str, page_ids: list[str] | None):
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        from app.analyzers.functional_doc import FunctionalDocAnalyzer
        analyzer = FunctionalDocAnalyzer(job_id)
        summary = await asyncio.to_thread(analyzer.run, space_key, page_ids)
        job_store.update_status(job_id, JobStatus.COMPLETED, summary=summary)
    except Exception as e:
        job_store.add_log(job_id, "error", str(e))
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


@router.get("/spaces/{space_key}/index")
def get_index(space_key: str):
    """Return the processing index for a space."""
    _require_unlocked()
    index_path = _output_root() / space_key / "_index.json"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail=f"No processed data for space '{space_key}'")
    return json.loads(index_path.read_text(encoding="utf-8"))


@router.get("/docs/{space_key}/{page_id}")
def get_doc(space_key: str, page_id: str):
    """Return a single structured document."""
    _require_unlocked()
    doc_path = _output_root() / space_key / "docs" / f"{page_id}.json"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"Document {page_id} not found")
    return json.loads(doc_path.read_text(encoding="utf-8"))
