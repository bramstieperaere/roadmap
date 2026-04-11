import asyncio
import json
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.analyzers.git_log_analyzer import run_ingest_commits
from app.analyzers.jira_issue_importer import run_import_jira_issues
from app.analyzers.jira_ticket_linker import run_link_jira_tickets
from app.analyzers.commit_classifier import run_classify_commits
from app.config import CONFIG_PATH, load_config
from app.job_store import job_store
from app.models_jobs import JobStatus, StartJobResponse
from app.session import session

router = APIRouter(prefix="/api/git-mining", tags=["git-mining"])

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_OUTPUT_DIR = CONFIG_PATH.parent / "git-mining"


class StartMiningRequest(BaseModel):
    action: str
    repo_names: list[str]
    branches: list[str] | None = None


class ClassifyCommitsRequest(BaseModel):
    commit_hashes: list[str]


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
    repos = []
    for name in request.repo_names:
        r = repo_map.get(name)
        if not r:
            raise HTTPException(status_code=400,
                                detail=f"Unknown repository: {name}")
        repos.append({"name": r.name, "path": r.path,
                       "processors": r.processors})

    if not repos:
        raise HTTPException(status_code=400, detail="No repositories selected")

    if request.action == "find_jira_projects":
        job = job_store.create_job(
            module_name=f"Find Jira Projects ({len(repos)} repo(s))",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": request.repo_names},
        )
        asyncio.create_task(_run_find_jira_projects(job.id, repos))
    elif request.action == "ingest_commits":
        branch_label = f", branches: {', '.join(request.branches)}" if request.branches else ""
        job = job_store.create_job(
            module_name=f"Ingest Git Commits ({len(repos)} repo(s){branch_label})",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": request.repo_names,
                    "branches": request.branches},
        )
        asyncio.create_task(run_ingest_commits(
            job.id, repos, request.branches))
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


@router.get("/processors")
def list_processors():
    """List available commit processors."""
    from app.analyzers.commit_processors import get_all_processors
    return [
        {"name": p.name, "label": p.label, "description": p.description,
         "node_property": p.node_property}
        for p in get_all_processors()
    ]


@router.post("/classify-commits", response_model=StartJobResponse)
async def classify_commits(request: ClassifyCommitsRequest):
    """Classify commits by type using AI (limited to 5)."""
    _require_unlocked()
    if not request.commit_hashes:
        raise HTTPException(status_code=400, detail="No commits provided")
    job = job_store.create_job(
        module_name=f"Classify Commits ({len(request.commit_hashes)})",
        module_type="git_mining",
        params={"commit_hashes": request.commit_hashes},
    )
    asyncio.create_task(run_classify_commits(job.id, request.commit_hashes))
    return StartJobResponse(
        job_id=job.id,
        message=f"Started classification for {len(request.commit_hashes)} commit(s)")


@router.get("/commits/{commit_hash}/tags")
def get_commit_tags(commit_hash: str):
    """Get classification tags for a commit."""
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    config = load_config_decrypted()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=config.neo4j.database) as s:
            result = s.run("""
                MATCH (c:Tooling:Commit {hash: $hash})-[:CLASSIFIED_AS]->(v:Facet:Value)
                RETURN v.name AS name, v.label AS label
                ORDER BY v.ordinal
            """, {"hash": commit_hash})
            return [dict(r) for r in result]
    finally:
        pass


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
def get_merge_source_commits(commit_hash: str, branch: str = ""):
    """Get the commits from the source branch of a merge commit."""
    _require_unlocked()
    from app.neo4j_client import get_neo4j_driver
    from app.config import load_config_decrypted
    config = load_config_decrypted()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=config.neo4j.database) as s:
            # Find the merge commit's second parent (feature branch tip)
            check = s.run("""
                MATCH (m:Tooling:Commit {hash: $hash})-[:PARENT {ord: 1}]->(tip:Tooling:Commit)
                RETURN tip.hash AS tip_hash
            """, {"hash": commit_hash}).single()
            if not check:
                raise HTTPException(status_code=404,
                                    detail="Not a merge commit or second parent not found")

            # Walk first-parent chain from tip, collect commits not on the target branch
            result = s.run("""
                MATCH (m:Tooling:Commit {hash: $hash})-[:PARENT {ord: 1}]->(tip:Tooling:Commit)
                MATCH p = (tip)-[:PARENT*0..200]->(anc:Tooling:Commit)
                WHERE ALL(rel IN relationships(p) WHERE rel.ord = 0)
                WITH nodes(p) AS chain
                UNWIND chain AS c
                WITH DISTINCT c
                WHERE $branch = '' OR NOT $branch IN coalesce(c.branches, [])
                RETURN c.hash AS hash, c.message AS message,
                       c.author_name AS author_name, c.author_email AS author_email,
                       c.date AS date, c.issue_keys AS issue_keys,
                       size(coalesce(c.files_changed, [])) AS files_count,
                       c.db_changes AS db_changes,
                       c.documented_files AS documented_files
                ORDER BY c.date ASC
            """, {"hash": commit_hash, "branch": branch})
            return [dict(r) for r in result]
    finally:
        pass


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
                       c.documented_files AS documented_files
                ORDER BY c.date DESC
            """, {"repo": repo_name, "branch": branch_name})
            return [dict(r) for r in result]
    finally:
        pass


@router.get("/results")
def get_results():
    _require_unlocked()
    out_path = _OUTPUT_DIR / "jira-projects.json"
    if not out_path.exists():
        return {"repos": {}, "summary": {"total_repos": 0,
                                          "total_commits": 0,
                                          "total_issue_keys": 0}}
    return json.loads(out_path.read_text(encoding="utf-8"))
