import asyncio
from importlib import import_module

from fastapi import APIRouter, HTTPException

from app.config import load_config_decrypted
from app.job_store import job_store
from app.models_jobs import (
    StartJobRequest, StartJobResponse,
    StartRepoRequest, StartRepoResponse,
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
    """Create an enricher instance from the registry."""
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
                analyzer = JavaMavenAnalyzer(job_id)
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


async def _run_repo_pipeline(
        repo_path: str,
        repo_name: str,
        analysis_tasks: list[dict],
        enrichment_job_id: str,
        modules_config: list[dict]):
    """Three-phase pipeline: code metamodel, technology detection,
    architecture enrichment."""
    # Phase 0: Clean existing repo data and create fresh Repository node
    # Must be single-threaded to avoid race conditions in parallel analysis
    driver = get_neo4j_driver()
    try:
        # Delete all Arch nodes linked to classes in this repo
        run_cypher_write(driver, """
            MATCH (r:Java:Repository {path: $repo_path})
                  -[:CONTAINS_MODULE]->(:Java:Module)
                  -[:CONTAINS_PACKAGE]->(:Java:Package)
                  -[:CONTAINS_CLASS]->(c:Java:Class)
            OPTIONAL MATCH (a:Arch)-[:IMPLEMENTED_BY]->(c)
            DETACH DELETE a
        """, {"repo_path": repo_path})
        # Delete all Arch nodes linked to methods in this repo
        run_cypher_write(driver, """
            MATCH (r:Java:Repository {path: $repo_path})
                  -[:CONTAINS_MODULE]->(:Java:Module)
                  -[:CONTAINS_PACKAGE]->(:Java:Package)
                  -[:CONTAINS_CLASS]->(:Java:Class)
                  -[:HAS_METHOD]->(meth:Java:Method)
            OPTIONAL MATCH (a:Arch)-[:IMPLEMENTED_BY]->(meth)
            DETACH DELETE a
        """, {"repo_path": repo_path})
        # Delete Arch:Microservice linked to this repo
        run_cypher_write(driver, """
            MATCH (r:Java:Repository {path: $repo_path})
            OPTIONAL MATCH (ms:Arch:Microservice)-[:IMPLEMENTED_BY]->(r)
            DETACH DELETE ms
        """, {"repo_path": repo_path})
        # Delete orphan Arch nodes
        run_cypher_write(driver, """
            MATCH (a:Arch)
            WHERE NOT EXISTS { MATCH (a)-[]-() }
            DELETE a
        """)
        # Delete all modules and their descendants for this repo
        run_cypher_write(driver, """
            MATCH (r:Java:Repository {path: $repo_path})
                  -[:CONTAINS_MODULE]->(m:Java:Module)
            OPTIONAL MATCH (m)-[*]->(n)
            DETACH DELETE n, m
        """, {"repo_path": repo_path})
        # Delete all Repository nodes with this path (cleans up any duplicates)
        run_cypher_write(driver, """
            MATCH (r:Java:Repository {path: $repo_path})
            DETACH DELETE r
        """, {"repo_path": repo_path})
        # Create fresh Repository node
        run_cypher_write(driver, """
            CREATE (r:Java:Repository {path: $repo_path, name: $repo_name})
        """, {"repo_path": repo_path, "repo_name": repo_name})
    finally:
        driver.close()

    # Phase 1: Run all module analysis jobs in parallel
    tasks = []
    for t in analysis_tasks:
        tasks.append(_run_job(
            t["job_id"], repo_path, t["module_name"],
            t["module_type"], t["relative_path"], repo_name))
    await asyncio.gather(*tasks)

    # Phase 1.5 + Phase 2: Technology detection and enrichment
    job_store.update_status(enrichment_job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()
        try:
            # Phase 1.5: Detect technologies per module
            from app.analyzers.technology_scanner import TechnologyScanner
            enrichment_modules = []
            all_technologies: set[str] = set()

            for mc in modules_config:
                if mc["technologies"]:
                    # Manual override: use configured technologies
                    techs = mc["technologies"]
                    job_store.add_log(
                        enrichment_job_id, "info",
                        f"{mc['module_name']}: using configured "
                        f"technologies {techs}")
                else:
                    # Auto-detect from Neo4j graph
                    scanner = TechnologyScanner(
                        enrichment_job_id, driver, mc["module_name"])
                    techs = await asyncio.to_thread(scanner.detect)

                if techs:
                    enrichment_modules.append({
                        "module_name": mc["module_name"],
                        "technologies": techs,
                    })
                    all_technologies.update(techs)

            # Create Arch:Microservice node
            if all_technologies:
                run_cypher_write(driver, """
                    MATCH (r:Java:Repository {path: $repo_path})
                    MERGE (ms:Arch:Microservice {name: $name})
                    SET ms.technologies = $techs
                    MERGE (ms)-[:IMPLEMENTED_BY]->(r)
                """, {
                    "repo_path": repo_path,
                    "name": repo_name or repo_path,
                    "techs": sorted(all_technologies),
                })

            # Phase 2: Run enrichers
            for em in enrichment_modules:
                for tech in em["technologies"]:
                    enricher = _create_enricher(
                        tech, enrichment_job_id, driver,
                        em["module_name"])
                    if enricher:
                        stats = await asyncio.to_thread(
                            enricher.enrich, [])
                        job_store.add_log(
                            enrichment_job_id, "info",
                            f"{em['module_name']}/{tech}: {stats}")
                    else:
                        job_store.add_log(
                            enrichment_job_id, "warn",
                            f"No enricher for technology: {tech}")
        finally:
            driver.close()
        job_store.update_status(
            enrichment_job_id, JobStatus.COMPLETED,
            summary="Architecture enrichment complete")
    except Exception as e:
        job_store.add_log(
            enrichment_job_id, "error",
            f"Enrichment failed: {e}")
        job_store.update_status(
            enrichment_job_id, JobStatus.FAILED, error=str(e))


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
        module.type, module.relative_path, repo.name))

    return StartJobResponse(
        job_id=job.id,
        message=f"Job started for module '{module.name}'")


@router.post("/start-repo", response_model=StartRepoResponse)
async def start_repo(request: StartRepoRequest):
    _require_unlocked()
    config = load_config_decrypted()

    if request.repo_index < 0 or request.repo_index >= len(config.repositories):
        raise HTTPException(status_code=400, detail="Invalid repository index")

    repo = config.repositories[request.repo_index]

    if not repo.modules:
        raise HTTPException(status_code=400, detail="No modules in repository")

    # Create Phase 1 jobs (one per module)
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

    # Always create enrichment job (auto-detection may find technologies)
    enrich_job = job_store.create_job(
        repo_path=repo.path,
        repo_index=request.repo_index,
        module_name="Architecture Enrichment",
        module_type="enrichment",
    )
    enrichment_job_id = enrich_job.id
    job_ids.append(enrichment_job_id)

    # Build modules config for technology detection
    modules_config = []
    for module in repo.modules:
        if module.type == "java":
            modules_config.append({
                "module_name": module.name,
                "technologies": list(module.technologies),
            })

    asyncio.create_task(_run_repo_pipeline(
        repo.path, repo.name, analysis_tasks,
        enrichment_job_id, modules_config))

    return StartRepoResponse(
        job_ids=job_ids,
        message=f"Started analysis pipeline for {len(repo.modules)} module(s)")


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
