import asyncio
import json
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from typing import Literal

from pydantic import BaseModel

from app.analyzers.git_log_analyzer import (
    run_ingest_commits, _load_ingestion_state)
from app.analyzers.jira_issue_importer import run_import_jira_issues
from app.analyzers.jira_ticket_linker import run_link_jira_tickets
from app.config import CONFIG_PATH, load_config
from app.job_store import job_store
from app.models_jobs import JobStatus, StartJobResponse
from app.session import session

router = APIRouter(prefix="/api/git-mining", tags=["git-mining"])

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_OUTPUT_DIR = CONFIG_PATH.parent / "git-mining"


class StartMiningRequest(BaseModel):
    action: str
    repo_names: list[str] = []
    branches: list[str] | None = None
    processing_config: str | None = None  # name of a GitProcessingConfig
    mode: Literal["incremental", "full"] = "incremental"



def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


async def _run_find_jira_projects(job_id: str, repos: list[dict]):
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        results = {}
        total_commits_all = 0
        total_keys_all = 0

        for repo in repos:
            repo_name = repo["name"]
            repo_path = repo["path"]
            job_store.add_log(job_id, "info",
                              f"Scanning git log for {repo_name}...")

            if not Path(repo_path).is_dir():
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: path does not exist: {repo_path}")
                continue

            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "log", "--format=%s"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log timed out after 120s")
                continue
            except Exception as e:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log failed: {e}")
                continue

            if proc.returncode != 0:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log returned "
                                  f"exit code {proc.returncode}: "
                                  f"{(proc.stderr or '').strip()}")
                continue

            stdout = proc.stdout or ""
            lines = stdout.strip().splitlines()
            total_commits = len(lines)

            projects: dict[str, dict] = {}
            total_keys = 0

            for line in lines:
                keys = _JIRA_KEY_RE.findall(line)
                for key in keys:
                    total_keys += 1
                    prefix = key.split("-")[0]
                    if prefix not in projects:
                        projects[prefix] = {
                            "issue_keys": set(),
                            "reference_count": 0,
                        }
                    projects[prefix]["issue_keys"].add(key)
                    projects[prefix]["reference_count"] += 1

            # Convert sets to sorted lists for JSON serialization
            projects_out = {}
            for prefix, data in sorted(projects.items()):
                keys_list = sorted(data["issue_keys"])
                projects_out[prefix] = {
                    "issue_keys": keys_list,
                    "reference_count": data["reference_count"],
                    "unique_issues": len(keys_list),
                }

            results[repo_name] = {
                "repo_name": repo_name,
                "repo_path": repo_path,
                "total_commits": total_commits,
                "total_issue_keys": total_keys,
                "projects": projects_out,
            }

            total_commits_all += total_commits
            total_keys_all += total_keys

            project_list = ", ".join(
                f"{p} ({d['unique_issues']} issues)"
                for p, d in sorted(projects_out.items())
            ) or "none"
            job_store.add_log(
                job_id, "info",
                f"{repo_name}: {total_commits} commits, "
                f"{total_keys} issue refs — projects: {project_list}")

        output = {
            "repos": results,
            "summary": {
                "total_repos": len(results),
                "total_commits": total_commits_all,
                "total_issue_keys": total_keys_all,
            },
        }

        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _OUTPUT_DIR / "jira-projects.json"
        out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

        job_store.add_log(job_id, "info",
                          f"Results written to {out_path}")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{len(results)} repo(s), "
                    f"{total_commits_all} commits, "
                    f"{total_keys_all} issue refs")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))


@router.post("/start", response_model=StartJobResponse)
async def start_mining(request: StartMiningRequest):
    _require_unlocked()

    valid_actions = {"find_jira_projects", "ingest_commits", "import_jira_issues", "link_jira_tickets"}
    if request.action not in valid_actions:
        raise HTTPException(status_code=400,
                            detail=f"Unknown action: {request.action}")

    config = load_config()
    repo_map = {r.name: r for r in config.repositories}

    # Resolve from processing config if provided
    repo_names = list(request.repo_names)
    branches = request.branches
    proc_config_name = request.processing_config
    if proc_config_name:
        pc = next((p for p in config.git_processing
                    if p.name == proc_config_name), None)
        if not pc:
            raise HTTPException(status_code=400,
                                detail=f"Unknown processing config: {proc_config_name}")
        repo_names = [pc.repo_name]
        branches = [pc.branch] if pc.branch else None

    repos = []
    for name in repo_names:
        r = repo_map.get(name)
        if not r:
            raise HTTPException(status_code=400,
                                detail=f"Unknown repository: {name}")
        # Processors: from processing config if available, else from repo config
        processors = r.processors
        if proc_config_name:
            pc = next(p for p in config.git_processing
                      if p.name == proc_config_name)
            # Resolve processors: from profile, from direct list, or both
            processors = list(pc.processors)
            if pc.profile:
                profile = next(
                    (pr for pr in config.processing_profiles
                     if pr.name == pc.profile), None)
                if profile:
                    for p_name in profile.processors:
                        if p_name not in processors:
                            processors.append(p_name)
        repos.append({"name": r.name, "path": r.path,
                       "processors": processors})

    if not repos:
        raise HTTPException(status_code=400, detail="No repositories selected")

    if request.action == "find_jira_projects":
        job = job_store.create_job(
            module_name=f"Find Jira Projects ({len(repos)} repo(s))",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": repo_names},
        )
        asyncio.create_task(_run_find_jira_projects(job.id, repos))
    elif request.action == "ingest_commits":
        label_parts = [f"{len(repos)} repo(s)"]
        if branches:
            label_parts.append(f"branches: {', '.join(branches)}")
        if proc_config_name:
            label_parts.append(f"config: {proc_config_name}")
        job = job_store.create_job(
            module_name=f"Ingest Git Commits ({', '.join(label_parts)})",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": repo_names,
                    "branches": branches,
                    "processing_config": proc_config_name},
        )
        asyncio.create_task(run_ingest_commits(
            job.id, repos, branches, mode=request.mode))
    elif request.action == "import_jira_issues":
        job = job_store.create_job(
            module_name=f"Import Jira Issues ({len(repos)} repo(s))",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": request.repo_names},
        )
        asyncio.create_task(run_import_jira_issues(job.id, repos))
    else:
        job = job_store.create_job(
            module_name=f"Link Jira Tickets ({len(repos)} repo(s))",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": request.repo_names},
        )
        asyncio.create_task(run_link_jira_tickets(job.id, repos))

    return StartJobResponse(
        job_id=job.id,
        message=f"Started git mining for {len(repos)} repo(s)")


@router.get("/processing-configs")
def list_processing_configs():
    """List all git processing configurations."""
    config = load_config()
    return [
        {"name": p.name, "repo_name": p.repo_name,
         "branch": p.branch, "processors": p.processors}
        for p in config.git_processing
    ]


@router.get("/processors")
def list_processors():
    """List all commit processors (matured + incubating)."""
    from app.analyzers.commit_processors import get_all_processors, get_incubating_processors
    result = []
    for p in get_all_processors():
        result.append({"name": p.name, "label": p.label,
                        "description": p.description,
                        "node_property": p.node_property,
                        "status": "matured",
                        "version": p.version})
    config = load_config()
    for p in get_incubating_processors(config):
        result.append({"name": p.name, "label": p.label,
                        "description": p.description,
                        "node_property": p.node_property,
                        "status": "incubating",
                        "version": p.version})
    return result


@router.get("/jobs/{job_id}/processor-suggestions")
def get_processor_suggestions(job_id: str):
    """Get AI-suggested processors from a completed ingestion job."""
    _require_unlocked()
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.params.get("processor_suggestions", {})


@router.get("/repos/{repo_name}/ingestion-state")
def get_ingestion_state(repo_name: str):
    """Return the current ingestion state for a repository."""
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    driver = get_neo4j_driver()
    state = _load_ingestion_state(driver, repo_name)
    if state is None:
        return {"has_state": False}
    return {"has_state": True, **state}


def _resolve_processors_for_config(config, gp) -> list[str]:
    """Resolve processor names for a git processing config."""
    repo = next((r for r in config.repositories if r.name == gp.repo_name), None)
    processors = list(gp.processors) if gp.processors else []
    if gp.profile:
        profile = next((p for p in config.processing_profiles
                        if p.name == gp.profile), None)
        if profile:
            for p_name in profile.processors:
                if p_name not in processors:
                    processors.append(p_name)
    if not processors and repo:
        processors = repo.processors
    return processors


@router.post("/check-remotes")
async def check_remotes(ingest: bool = False):
    """Check all git processing configs for new remote commits.

    Compares remote branch tip (via git ls-remote) with stored ingestion
    state tip. When ingest=True, starts incremental ingestion jobs for
    configs that have new commits.
    """
    import logging
    log = logging.getLogger("roadmap.polling")

    _require_unlocked()
    config = load_config()

    if not config.git_processing:
        return {"checked": 0, "results": [],
                "message": "No git processing configs"}

    repo_map = {r.name: r for r in config.repositories}
    driver = None
    results = []
    jobs_started = []

    for gp in config.git_processing:
        repo = repo_map.get(gp.repo_name)
        if not repo:
            log.warning("check-remotes: repo %s not found in config",
                        gp.repo_name)
            continue
        if not gp.branch:
            continue
        repo_path = repo.path
        if not Path(repo_path).is_dir():
            log.warning("check-remotes: %s path does not exist: %s",
                        gp.repo_name, repo_path)
            continue

        # Get remote tip via ls-remote (network call)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "ls-remote", "--heads", "origin", gp.branch],
                cwd=repo_path,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=30,
            )
        except Exception as e:
            log.error("check-remotes: %s/%s ls-remote failed: %s",
                      gp.repo_name, gp.branch, e)
            results.append({"name": gp.name, "status": "error",
                            "detail": str(e)})
            continue

        if proc.returncode != 0 or not proc.stdout.strip():
            log.warning("check-remotes: %s/%s ls-remote returned nothing",
                        gp.repo_name, gp.branch)
            results.append({"name": gp.name, "status": "no_remote",
                            "detail": "Branch not found on remote"})
            continue

        remote_tip = proc.stdout.strip().split()[0][:12]

        # Get stored tip from ingestion state
        if driver is None:
            from app.neo4j_client import get_neo4j_driver
            driver = get_neo4j_driver()
        state = _load_ingestion_state(driver, gp.repo_name)
        stored_tip = None
        if state:
            stored_tip = state.get("branch_tips", {}).get(gp.branch)

        has_changes = remote_tip != stored_tip
        entry = {
            "name": gp.name,
            "repo_name": gp.repo_name,
            "branch": gp.branch,
            "remote_tip": remote_tip,
            "stored_tip": stored_tip,
            "has_changes": has_changes,
            "status": "changed" if has_changes else "up_to_date",
        }

        if has_changes:
            log.info("check-remotes: %s — NEW COMMITS "
                     "(remote=%s, stored=%s)",
                     gp.name, remote_tip, stored_tip or "none")

            if ingest:
                processors = _resolve_processors_for_config(config, gp)
                repos = [{"name": repo.name, "path": repo.path,
                          "processors": processors}]
                branches = [gp.branch]
                job = job_store.create_job(
                    module_name=f"Incremental Ingest ({gp.name})",
                    module_type="git_mining",
                    params={"action": "ingest_commits",
                            "repo_names": [gp.repo_name],
                            "branches": branches,
                            "processing_config": gp.name,
                            "triggered_by": "polling"},
                )
                asyncio.create_task(run_ingest_commits(
                    job.id, repos, branches, mode="incremental"))
                entry["job_id"] = job.id
                entry["status"] = "ingestion_started"
                jobs_started.append(job.id)
                log.info("check-remotes: %s — started ingestion job %s",
                         gp.name, job.id)
        else:
            log.info("check-remotes: %s — up to date (%s)",
                     gp.name, remote_tip)

        results.append(entry)

    changed = sum(1 for r in results if r.get("has_changes"))
    log.info("check-remotes: checked %d config(s), %d with changes, "
             "%d jobs started",
             len(results), changed, len(jobs_started))

    return {"checked": len(results), "changed": changed,
            "jobs_started": jobs_started,
            "results": results}


@router.post("/incubating-processors")
def create_incubating_processor(body: dict):
    """Create a new incubating processor from an AI suggestion or manual input."""
    _require_unlocked()
    from app.models import IncubatingProcessorConfig
    config = load_config()
    existing_names = {p.name for p in config.incubating_processors}
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if name in existing_names:
        raise HTTPException(status_code=400, detail=f"Processor '{name}' already exists")

    new_proc = IncubatingProcessorConfig(
        name=name,
        label=body.get("label", name),
        description=body.get("description", ""),
        instructions=body.get("instructions", ""),
        file_patterns=body.get("file_patterns", []),
        instance_count=0,
    )
    config.incubating_processors.append(new_proc)

    from app.config import save_config
    save_config(config)
    return {"status": "created", "name": name}


@router.delete("/incubating-processors/{name}")
def delete_incubating_processor(name: str):
    """Delete an incubating processor."""
    _require_unlocked()
    config = load_config()
    before = len(config.incubating_processors)
    config.incubating_processors = [
        p for p in config.incubating_processors if p.name != name]
    if len(config.incubating_processors) == before:
        raise HTTPException(status_code=404, detail=f"Processor '{name}' not found")

    from app.config import save_config
    save_config(config)
    return {"status": "deleted", "name": name}


@router.put("/incubating-processors/{name}")
def update_incubating_processor(name: str, body: dict):
    """Update an incubating processor's configuration."""
    _require_unlocked()
    config = load_config()
    proc = next((p for p in config.incubating_processors if p.name == name), None)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Processor '{name}' not found")

    if "label" in body:
        proc.label = body["label"]
    if "description" in body:
        proc.description = body["description"]
    if "instructions" in body:
        proc.instructions = body["instructions"]
    if "file_patterns" in body:
        proc.file_patterns = body["file_patterns"]

    from app.config import save_config
    save_config(config)
    return {"status": "updated", "name": name}


@router.get("/branches/{repo_name}")
def get_branches(repo_name: str):
    """List local branches for a configured repository (from git)."""
    _require_unlocked()
    config = load_config()
    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")
    if not Path(repo.path).is_dir():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {repo.path}")
    from app.analyzers.git_log_analyzer import _discover_branches
    branches = _discover_branches(repo.path)
    return [b["name"] for b in branches]


@router.get("/repos/{repo_name}/branches")
def get_neo4j_branches(repo_name: str):
    """List branches for a repository from Neo4j (already ingested)."""
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    config = load_config_decrypted()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=config.neo4j.database) as s:
            result = s.run("""
                MATCH (:Tooling:Repository {name: $repo})-[:HAS_BRANCH]->(b:Tooling:Branch)
                OPTIONAL MATCH (b)-[:TIP]->(tip:Tooling:Commit)
                RETURN b.name AS name, tip.date AS latest_date,
                       tip.hash AS tip_hash
                ORDER BY b.name
            """, {"repo": repo_name})
            return [dict(r) for r in result]
    finally:
        pass


@router.get("/commits/{commit_hash}/merge-source")
def get_merge_source_commits(commit_hash: str, repo_name: str = "",
                             branch: str = ""):
    """Get the commits from the source branch of a merge commit.

    Uses git log to accurately determine which commits were introduced
    by the merge (hash^2 --not hash^1), then looks them up in Neo4j.
    """
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    config = load_config_decrypted()
    driver = get_neo4j_driver()

    # Resolve full hash and repo from Neo4j
    with driver.session(database=config.neo4j.database) as s:
        row = s.run("""
            MATCH (c:Tooling:Commit {hash: $hash})
            RETURN c.full_hash AS full_hash, c.repo_name AS repo_name
        """, {"hash": commit_hash}).single()
        if not row:
            raise HTTPException(status_code=404, detail="Commit not found")
        full_hash = row["full_hash"] or commit_hash
        neo4j_repo = row["repo_name"] or repo_name

    # Find repo path
    cfg = load_config()
    repo = next((r for r in cfg.repositories if r.name == neo4j_repo), None)
    if not repo or not Path(repo.path).is_dir():
        # Fall back to Neo4j-only approach
        return _merge_source_from_neo4j(driver, config, commit_hash)

    # Use git to get exact merge source commits
    try:
        proc = subprocess.run(
            ["git", "log", f"{full_hash}^2", "--not", f"{full_hash}^1",
             "--format=%H"],
            cwd=repo.path,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        if proc.returncode != 0:
            return _merge_source_from_neo4j(driver, config, commit_hash)

        hashes = [h[:12] for h in proc.stdout.strip().splitlines() if h.strip()]
        if not hashes:
            return []
    except Exception:
        return _merge_source_from_neo4j(driver, config, commit_hash)

    # Look up commits in Neo4j
    with driver.session(database=config.neo4j.database) as s:
        result = s.run("""
            UNWIND $hashes AS h
            MATCH (c:Tooling:Commit {hash: h})
            RETURN c.hash AS hash, c.message AS message,
                   c.author_name AS author_name, c.author_email AS author_email,
                   c.date AS date, c.issue_keys AS issue_keys,
                   size(coalesce(c.files_changed, [])) AS files_count,
                   c.db_changes AS db_changes,
                   c.jpa_entities AS jpa_entities,
                   c.spring_endpoints AS spring_endpoints,
                   c.spring_messaging AS spring_messaging,
                   c.spring_datasource AS spring_datasource,
                   c.documented_files AS documented_files,
                   c.uncovered_files AS uncovered_files
            ORDER BY c.date ASC
        """, {"hashes": hashes})
        return [dict(r) for r in result]


def _merge_source_from_neo4j(driver, config, commit_hash: str) -> list[dict]:
    """Fallback: get merge source using Neo4j graph traversal."""
    with driver.session(database=config.neo4j.database) as s:
        check = s.run("""
            MATCH (m:Tooling:Commit {hash: $hash})-[:PARENT {ord: 1}]->(tip:Tooling:Commit)
            MATCH (m)-[:PARENT {ord: 0}]->(fp:Tooling:Commit)
            RETURN tip.hash AS tip_hash, fp.hash AS first_parent
        """, {"hash": commit_hash}).single()
        if not check:
            return []

        result = s.run("""
            MATCH (m:Tooling:Commit {hash: $hash})-[:PARENT {ord: 1}]->(tip:Tooling:Commit)
            MATCH (m)-[:PARENT {ord: 0}]->(fp:Tooling:Commit)
            MATCH p = (tip)-[:PARENT*0..200]->(anc:Tooling:Commit)
            WHERE ALL(rel IN relationships(p) WHERE rel.ord = 0)
            WITH nodes(p) AS chain, fp
            UNWIND chain AS c
            WITH DISTINCT c, fp
            WHERE c.hash <> fp.hash
            RETURN c.hash AS hash, c.message AS message,
                   c.author_name AS author_name, c.author_email AS author_email,
                   c.date AS date, c.issue_keys AS issue_keys,
                   size(coalesce(c.files_changed, [])) AS files_count,
                   c.db_changes AS db_changes,
                   c.jpa_entities AS jpa_entities,
                   c.spring_endpoints AS spring_endpoints,
                   c.spring_messaging AS spring_messaging,
                   c.spring_datasource AS spring_datasource,
                   c.documented_files AS documented_files,
                   c.uncovered_files AS uncovered_files
            ORDER BY c.date ASC
        """, {"hash": commit_hash})
        return [dict(r) for r in result]


@router.get("/repos/{repo_name}/branches/{branch_name}/commits")
def get_branch_commits(repo_name: str, branch_name: str):
    """Get all commits on a branch from Neo4j, ordered by date descending."""
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    config = load_config_decrypted()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=config.neo4j.database) as s:
            result = s.run("""
                MATCH (:Tooling:Repository {name: $repo})-[:HAS_COMMIT]->(c:Tooling:Commit)
                WHERE $branch IN c.branches
                RETURN c.hash AS hash, c.message AS message,
                       c.author_name AS author_name, c.author_email AS author_email,
                       c.date AS date, c.issue_keys AS issue_keys,
                       size(c.files_changed) AS files_count,
                       c.db_changes AS db_changes,
                       c.jpa_entities AS jpa_entities,
                       c.spring_endpoints AS spring_endpoints,
                       c.spring_messaging AS spring_messaging,
                       c.spring_datasource AS spring_datasource,
                       c.documented_files AS documented_files,
                       c.uncovered_files AS uncovered_files
                ORDER BY c.date DESC
            """, {"repo": repo_name, "branch": branch_name})
            return [dict(r) for r in result]
    finally:
        pass


@router.get("/commits/{commit_hash}/rollup")
def get_merge_rollup(commit_hash: str, repo_name: str = ""):
    """Get combined processor data for a merge commit (rolled up from all source commits)."""
    _require_unlocked()
    # Get merge source commits (reuse the existing endpoint logic)
    source_commits = get_merge_source_commits(commit_hash, repo_name)
    if not source_commits:
        return {}

    processor_fields = [
        "db_changes", "jpa_entities", "spring_endpoints",
        "spring_messaging", "spring_datasource",
    ]

    rollup: dict = {}
    for field in processor_fields:
        items = []
        for commit in source_commits:
            raw = commit.get(field)
            if raw:
                try:
                    items.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass
        if items:
            rollup[field] = _merge_processor_results(items, field)

    # Combine all files
    all_files: set[str] = set()
    for commit in source_commits:
        doc = commit.get("documented_files")
        if doc:
            all_files.update(doc)

    rollup["documented_files"] = sorted(all_files)
    rollup["commit_count"] = len(source_commits)
    rollup["total_files"] = sum(c.get("files_count", 0) for c in source_commits)
    return rollup


def _merge_processor_results(items: list[dict], field: str) -> dict:
    """Merge multiple processor results into a single rolled-up view."""
    merged: dict = {"files": []}
    all_files: set[str] = set()
    for item in items:
        all_files.update(item.get("files", []))

    merged["files"] = sorted(all_files)

    if field == "db_changes":
        merged["changes"] = []
        for item in items:
            merged["changes"].extend(item.get("changes", []))

    elif field == "jpa_entities":
        by_class: dict[str, dict] = {}
        for item in items:
            for e in item.get("entities", []):
                by_class[e.get("class", "")] = e
        merged["entities"] = list(by_class.values())

    elif field == "spring_endpoints":
        by_ctrl: dict[str, dict] = {}
        for item in items:
            for c in item.get("controllers", []):
                by_ctrl[c.get("controller", "")] = c
        merged["controllers"] = list(by_ctrl.values())
        merged["endpoint_count"] = sum(
            len(c.get("endpoints", [])) for c in merged["controllers"])

    elif field == "spring_messaging":
        by_class: dict[str, dict] = {}
        for item in items:
            for c in item.get("components", []):
                by_class[c.get("class", "")] = c
        merged["components"] = list(by_class.values())
        merged["incoming_count"] = sum(
            len(c.get("incoming", [])) for c in merged["components"])
        merged["outgoing_count"] = sum(
            len(c.get("outgoing", [])) for c in merged["components"])

    elif field == "spring_datasource":
        seen: set[str] = set()
        merged["datasources"] = []
        for item in items:
            for d in item.get("datasources", []):
                key = d.get("url", "") or d.get("driver", "")
                if key not in seen:
                    seen.add(key)
                    merged["datasources"].append(d)

    return merged


def _get_diff_lines(repo_path: str, full_hash: str,
                    file_path: str) -> tuple[list[int], list[int]]:
    """Get added and removed line numbers for a file at a commit.

    Returns (added_lines, removed_lines) where each is a list of
    1-based line numbers in the file at this commit.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", f"{full_hash}^", full_hash, "--unified=0",
             "--", file_path],
            cwd=repo_path,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
        if proc.returncode != 0:
            return [], []

        added: list[int] = []
        removed: list[int] = []
        current_new_line = 0

        for line in proc.stdout.splitlines():
            if line.startswith("@@"):
                # Parse @@ -old,count +new,count @@
                import re
                m = re.search(r"\+(\d+)(?:,(\d+))?", line)
                if m:
                    current_new_line = int(m.group(1))
                    count = int(m.group(2)) if m.group(2) else 1
                    if count == 0:
                        # Pure deletion at this position
                        removed.append(current_new_line)
                    continue
            elif line.startswith("+") and not line.startswith("+++"):
                added.append(current_new_line)
                current_new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                # Removed lines don't advance the new-file line counter
                pass
            else:
                current_new_line += 1

        return added, removed
    except Exception:
        return [], []


@router.get("/file-at-commit")
def get_file_at_commit(repo_name: str, commit_hash: str, file_path: str):
    """Get file content at a specific commit with diff highlighting info."""
    _require_unlocked()
    config = load_config()
    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")
    if not Path(repo.path).is_dir():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {repo.path}")

    # Resolve full hash from Neo4j
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    cfg = load_config_decrypted()
    driver = get_neo4j_driver()
    full_hash = commit_hash
    with driver.session(database=cfg.neo4j.database) as s:
        row = s.run(
            "MATCH (c:Tooling:Commit {hash: $hash}) RETURN c.full_hash AS fh",
            {"hash": commit_hash}).single()
        if row and row["fh"]:
            full_hash = row["fh"]

    try:
        proc = subprocess.run(
            ["git", "show", f"{full_hash}:{file_path}"],
            cwd=repo.path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=404,
                                detail=f"File not found at commit: {file_path}")

        # Get diff against parent to find changed lines
        added_lines, removed_lines = _get_diff_lines(
            repo.path, full_hash, file_path)

        return {"path": file_path, "content": proc.stdout,
                "commit_hash": commit_hash,
                "added_lines": added_lines,
                "removed_lines": removed_lines}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git show timed out")


@router.get("/results")
def get_results():
    _require_unlocked()
    out_path = _OUTPUT_DIR / "jira-projects.json"
    if not out_path.exists():
        return {"repos": {}, "summary": {"total_repos": 0,
                                          "total_commits": 0,
                                          "total_issue_keys": 0}}
    return json.loads(out_path.read_text(encoding="utf-8"))
