import asyncio
from datetime import datetime, timezone
from importlib import import_module

from fastapi import APIRouter, HTTPException

from app.config import load_config_decrypted
from app.job_store import job_store
from app.models_jobs import (
    StartJobRequest, StartJobResponse,
    StartRepoRequest, StartRepoResponse,
    StartPipelineRequest, StartPipelineResponse,
    JobListResponse, JobSummary, JobDetailResponse, JobStatus,
)
from app.neo4j_client import get_neo4j_driver, run_cypher_write
from app.session import session

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Registry: tech key -> (module_path, class_name)
_ENRICHER_REGISTRY: dict[str, tuple[str, str]] = {
    "spring-web": (
        "app.analyzers.enrichers.spring_web", "SpringWebEnricher"),
    "spring-jms": (
        "app.analyzers.enrichers.spring_jms", "SpringJmsEnricher"),
    "spring-scheduled": (
        "app.analyzers.enrichers.spring_scheduled",
        "SpringScheduledEnricher"),
    "feign": (
        "app.analyzers.enrichers.feign_client", "FeignClientEnricher"),
    "rest-clients": (
        "app.analyzers.enrichers.rest_client", "RestClientEnricher"),
    "spring-data": (
        "app.analyzers.enrichers.spring_data", "SpringDataEnricher"),
}


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


def _create_enricher(tech: str, job_id: str, driver, module_name: str):
    entry = _ENRICHER_REGISTRY.get(tech)
    if not entry:
        return None
    mod = import_module(entry[0])
    cls = getattr(mod, entry[1])
    return cls(job_id, driver, module_name)


async def _run_job(job_id: str, repo_path: str,
                   module_name: str, module_type: str,
                   relative_path: str, repo_name: str = ""):
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()
        try:
            if module_type == "java":
                from app.analyzers.java_maven import JavaMavenAnalyzer
                analyzer = JavaMavenAnalyzer(job_id, job_type="analysis")
            else:
                job_store.add_log(job_id, "error",
                                  f"Unsupported module type: {module_type}")
                job_store.update_status(job_id, JobStatus.FAILED,
                                        error=f"Unsupported module type: {module_type}")
                return

            summary = await asyncio.to_thread(
                analyzer.run, repo_path, module_name,
                relative_path, driver, repo_name)

            job_store.update_status(job_id, JobStatus.COMPLETED,
                                    summary=summary)
        finally:
            driver.close()
    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


def _cleanup_repo(driver, repo_path: str, repo_name: str, job_id: str = ""):
    """Phase 0: wipe existing graph data for a repo and recreate root node."""
    run_cypher_write(driver, """
        MATCH (r:Java:Repository {path: $repo_path})
              -[:CONTAINS_MODULE]->(:Java:Module)
              -[:CONTAINS_PACKAGE]->(:Java:Package)
              -[:CONTAINS_CLASS]->(c:Java:Class)
        OPTIONAL MATCH (a:Arch)-[:IMPLEMENTED_BY]->(c)
        DETACH DELETE a
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        MATCH (r:Java:Repository {path: $repo_path})
              -[:CONTAINS_MODULE]->(:Java:Module)
              -[:CONTAINS_PACKAGE]->(:Java:Package)
              -[:CONTAINS_CLASS]->(:Java:Class)
              -[:HAS_METHOD]->(meth:Java:Method)
        OPTIONAL MATCH (a:Arch)-[:IMPLEMENTED_BY]->(meth)
        DETACH DELETE a
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        MATCH (ms:Arch:Microservice)
              -[:IMPLEMENTED_BY]->(:Java:Repository {path: $repo_path})
        MATCH (ds:Data:Service)-[:MAPS_TO]->(ms)
        OPTIONAL MATCH (ds)-[]->(dc:Data)
        DETACH DELETE dc, ds
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        MATCH (d:Data)
        WHERE NOT EXISTS { MATCH (d)-[]-() }
        DELETE d
    """)
    run_cypher_write(driver, """
        MATCH (r:Java:Repository {path: $repo_path})
        OPTIONAL MATCH (ms:Arch:Microservice)-[:IMPLEMENTED_BY]->(r)
        DETACH DELETE ms
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        MATCH (a:Arch)
        WHERE NOT EXISTS { MATCH (a)-[]-() }
        DELETE a
    """)
    run_cypher_write(driver, """
        MATCH (r:Java:Repository {path: $repo_path})
              -[:CONTAINS_MODULE]->(m:Java:Module)
        OPTIONAL MATCH (m)-[*]->(n)
        DETACH DELETE n, m
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        MATCH (r:Java:Repository {path: $repo_path})
        DETACH DELETE r
    """, {"repo_path": repo_path})
    run_cypher_write(driver, """
        CREATE (r:Java:Repository {
            path: $repo_path, name: $repo_name,
            created_at: $created_at, job_id: $job_id, job_type: $job_type
        })
    """, {
        "repo_path": repo_path, "repo_name": repo_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id, "job_type": "analysis",
    })


async def _run_code_analysis(repo_path: str, repo_name: str,
                              analysis_tasks: list[dict]):
    """Phase 0 (cleanup) + Phase 1 (parallel module parsing)."""
    driver = get_neo4j_driver()
    try:
        cleanup_job_id = analysis_tasks[0]["job_id"] if analysis_tasks else ""
        _cleanup_repo(driver, repo_path, repo_name, cleanup_job_id)
    finally:
        driver.close()

    await asyncio.gather(*[
        _run_job(t["job_id"], repo_path, t["module_name"],
                 t["module_type"], t["relative_path"], repo_name)
        for t in analysis_tasks
    ])


async def _run_enrichment(repo_path: str, repo_name: str,
                           modules_config: list[dict], job_id: str):
    """Phase 1.5 (technology detection) + Phase 2 (architecture enrichers)."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()
        try:
            from app.analyzers.technology_scanner import TechnologyScanner
            enrichment_modules = []
            all_technologies: set[str] = set()

            for mc in modules_config:
                if mc["technologies"]:
                    techs = mc["technologies"]
                    job_store.add_log(
                        job_id, "info",
                        f"{mc['module_name']}: using configured technologies {techs}")
                else:
                    scanner = TechnologyScanner(job_id, driver, mc["module_name"])
                    techs = await asyncio.to_thread(scanner.detect)

                if techs:
                    enrichment_modules.append({
                        "module_name": mc["module_name"],
                        "technologies": techs,
                    })
                    all_technologies.update(techs)

            if all_technologies:
                run_cypher_write(driver, """
                    MATCH (r:Java:Repository {path: $repo_path})
                    MERGE (ms:Arch:Microservice {name: $name})
                    ON CREATE SET ms.created_at = $created_at,
                                  ms.job_id = $job_id,
                                  ms.job_type = $job_type
                    SET ms.technologies = $techs
                    MERGE (ms)-[:IMPLEMENTED_BY]->(r)
                """, {
                    "repo_path": repo_path,
                    "name": repo_name or repo_path,
                    "techs": sorted(all_technologies),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "job_id": job_id,
                    "job_type": "enrichment",
                })

            for em in enrichment_modules:
                for tech in em["technologies"]:
                    enricher = _create_enricher(tech, job_id, driver, em["module_name"])
                    if enricher:
                        stats = await asyncio.to_thread(enricher.enrich, [])
                        job_store.add_log(job_id, "info",
                                          f"{em['module_name']}/{tech}: {stats}")
                    else:
                        job_store.add_log(job_id, "warn",
                                          f"No enricher for technology: {tech}")
        finally:
            driver.close()
        job_store.update_status(job_id, JobStatus.COMPLETED,
                                summary="Architecture enrichment complete")
    except Exception as e:
        job_store.add_log(job_id, "error", f"Enrichment failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


async def _run_data_flow(repo_name: str, job_id: str):
    """Phase 3: data flow enrichment."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        config = load_config_decrypted()
        db_overrides = {
            db.repo_type: {"name": db.name, "technology": db.technology}
            for db in config.databases
        }
        driver = get_neo4j_driver()
        try:
            from app.analyzers.enrichers.data_flow import DataFlowEnricher
            enricher = DataFlowEnricher(job_id, driver, repo_name, db_overrides)
            stats = await asyncio.to_thread(enricher.enrich)
            job_store.add_log(job_id, "info", f"DataFlow: {stats}")
        finally:
            driver.close()
        job_store.update_status(job_id, JobStatus.COMPLETED,
                                summary="Data flow enrichment complete")
    except Exception as e:
        job_store.add_log(job_id, "error", f"Data flow failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


async def _run_full_pipeline(repo_path: str, repo_name: str,
                              analysis_tasks: list[dict],
                              enrichment_job_id: str,
                              data_flow_job_id: str,
                              modules_config: list[dict]):
    """Full pipeline: code analysis → enrichment → data flow."""
    await _run_code_analysis(repo_path, repo_name, analysis_tasks)
    await _run_enrichment(repo_path, repo_name, modules_config, enrichment_job_id)
    await _run_data_flow(repo_name, data_flow_job_id)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start", response_model=StartJobResponse)
async def start_job(request: StartJobRequest):
    """Start a single module analysis job."""
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
        module.type, module.relative_path, repo.name))

    return StartJobResponse(
        job_id=job.id,
        message=f"Job started for module '{module.name}'")


@router.post("/start-repo", response_model=StartRepoResponse)
async def start_repo(request: StartRepoRequest):
    """Full pipeline for a single repo (analysis → enrichment → data flow)."""
    _require_unlocked()
    config = load_config_decrypted()

    if request.repo_index < 0 or request.repo_index >= len(config.repositories):
        raise HTTPException(status_code=400, detail="Invalid repository index")

    repo = config.repositories[request.repo_index]

    if not repo.modules:
        raise HTTPException(status_code=400, detail="No modules in repository")

    analysis_tasks = []
    job_ids = []
    for module in repo.modules:
        job = job_store.create_job(
            repo_path=repo.path,
            repo_index=request.repo_index,
            module_name=module.name,
            module_type=module.type,
        )
        job_ids.append(job.id)
        analysis_tasks.append({
            "job_id": job.id,
            "module_name": module.name,
            "module_type": module.type,
            "relative_path": module.relative_path,
        })

    enrich_job = job_store.create_job(
        repo_path=repo.path,
        repo_index=request.repo_index,
        module_name="Architecture Enrichment",
        module_type="enrichment",
    )
    job_ids.append(enrich_job.id)

    df_job = job_store.create_job(
        repo_path=repo.path,
        repo_index=request.repo_index,
        module_name="Data Flow Enrichment",
        module_type="data-flow",
    )
    job_ids.append(df_job.id)

    modules_config = [
        {"module_name": m.name, "technologies": list(m.technologies)}
        for m in repo.modules if m.type == "java"
    ]

    asyncio.create_task(_run_full_pipeline(
        repo.path, repo.name, analysis_tasks,
        enrich_job.id, df_job.id, modules_config))

    return StartRepoResponse(
        job_ids=job_ids,
        message=f"Started full pipeline for {len(repo.modules)} module(s)")


@router.post("/start-analysis", response_model=StartPipelineResponse)
async def start_analysis(request: StartPipelineRequest):
    """Phase 0+1: clean graph and parse source code for one or more repos."""
    _require_unlocked()
    config = load_config_decrypted()

    all_job_ids = []
    for repo_index in request.repo_indices:
        if repo_index < 0 or repo_index >= len(config.repositories):
            raise HTTPException(status_code=400,
                                detail=f"Invalid repository index: {repo_index}")
        repo = config.repositories[repo_index]
        if not repo.modules:
            raise HTTPException(status_code=400,
                                detail=f"Repository '{repo.name}' has no modules configured")

        analysis_tasks = []
        for module in repo.modules:
            job = job_store.create_job(
                repo_path=repo.path,
                repo_index=repo_index,
                module_name=module.name,
                module_type=module.type,
            )
            all_job_ids.append(job.id)
            analysis_tasks.append({
                "job_id": job.id,
                "module_name": module.name,
                "module_type": module.type,
                "relative_path": module.relative_path,
            })
        asyncio.create_task(_run_code_analysis(repo.path, repo.name, analysis_tasks))

    return StartPipelineResponse(
        job_ids=all_job_ids,
        message=f"Code analysis started for {len(request.repo_indices)} repo(s)")


@router.post("/start-enrichment", response_model=StartPipelineResponse)
async def start_enrichment(request: StartPipelineRequest):
    """Phase 1.5+2: technology detection and architecture enrichment."""
    _require_unlocked()
    config = load_config_decrypted()

    all_job_ids = []
    for repo_index in request.repo_indices:
        if repo_index < 0 or repo_index >= len(config.repositories):
            raise HTTPException(status_code=400,
                                detail=f"Invalid repository index: {repo_index}")
        repo = config.repositories[repo_index]

        modules_config = [
            {"module_name": m.name, "technologies": list(m.technologies)}
            for m in repo.modules if m.type == "java"
        ]

        job = job_store.create_job(
            repo_path=repo.path,
            repo_index=repo_index,
            module_name="Architecture Enrichment",
            module_type="enrichment",
        )
        all_job_ids.append(job.id)
        asyncio.create_task(_run_enrichment(repo.path, repo.name, modules_config, job.id))

    return StartPipelineResponse(
        job_ids=all_job_ids,
        message=f"Enrichment started for {len(request.repo_indices)} repo(s)")


@router.post("/start-data-flow", response_model=StartPipelineResponse)
async def start_data_flow(request: StartPipelineRequest):
    """Phase 3: data flow enrichment."""
    _require_unlocked()
    config = load_config_decrypted()

    all_job_ids = []
    for repo_index in request.repo_indices:
        if repo_index < 0 or repo_index >= len(config.repositories):
            raise HTTPException(status_code=400,
                                detail=f"Invalid repository index: {repo_index}")
        repo = config.repositories[repo_index]

        job = job_store.create_job(
            repo_path=repo.path,
            repo_index=repo_index,
            module_name="Data Flow Enrichment",
            module_type="data-flow",
        )
        all_job_ids.append(job.id)
        asyncio.create_task(_run_data_flow(repo.name, job.id))

    return StartPipelineResponse(
        job_ids=all_job_ids,
        message=f"Data flow started for {len(request.repo_indices)} repo(s)")


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
            params=j.params,
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
