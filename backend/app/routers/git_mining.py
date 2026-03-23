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
        repos.append({"name": r.name, "path": r.path})

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
        job = job_store.create_job(
            module_name=f"Ingest Git Commits ({len(repos)} repo(s))",
            module_type="git_mining",
            params={"action": request.action,
                    "repo_names": request.repo_names},
        )
        asyncio.create_task(run_ingest_commits(job.id, repos))
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


@router.get("/results")
def get_results():
    _require_unlocked()
    out_path = _OUTPUT_DIR / "jira-projects.json"
    if not out_path.exists():
        return {"repos": {}, "summary": {"total_repos": 0,
                                          "total_commits": 0,
                                          "total_issue_keys": 0}}
    return json.loads(out_path.read_text(encoding="utf-8"))
