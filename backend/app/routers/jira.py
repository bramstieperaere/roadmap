import time
import urllib.parse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config_decrypted, save_config, has_encrypted_fields
from app.session import session
from app.atlassian_client import atlassian_request, require_atlassian_configured
from app.jira_cache import JiraCache
from app.jira_issue_service import JiraIssueService

router = APIRouter(prefix="/api/jira", tags=["jira"])


class RefreshIssuesRequest(BaseModel):
    issue_keys: list[str]


def _require_unlocked():
    if has_encrypted_fields() and not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked.")


def _get_cache() -> tuple:
    """Return (atlassian_config, JiraCache, JiraIssueService)."""
    config = load_config_decrypted()
    atl = config.atlassian
    require_atlassian_configured(atl)
    cache = JiraCache(atl.cache_dir, atl.refresh_duration)
    return atl, cache, JiraIssueService(atl, cache)


def _find_project(atl, project_key: str):
    for p in atl.jira_projects:
        if p.key.upper() == project_key.upper():
            return p
    raise HTTPException(status_code=404, detail=f"Project '{project_key}' not configured")


def _jql_search(atl, jql: str, fields: str, start_at: int = 0, max_results: int = 100) -> dict:
    """Run a JQL search, using v3 API for Cloud and v2 for Data Center."""
    encoded_jql = urllib.parse.quote(jql)
    if atl.deployment_type == "cloud":
        return atlassian_request(
            atl,
            f"/rest/api/3/search/jql?jql={encoded_jql}&startAt={start_at}"
            f"&maxResults={max_results}&fields={fields}",
        )
    return atlassian_request(
        atl,
        f"/rest/api/2/search?jql={encoded_jql}&startAt={start_at}"
        f"&maxResults={max_results}&fields={fields}",
    )


def _build_issue_list(issue_data: dict) -> list:
    issues = []
    for raw in issue_data.get("issues", []):
        fields = raw.get("fields", {})
        issues.append({
            "key": raw["key"],
            "summary": fields.get("summary", ""),
            "status": fields.get("status", {}).get("name", ""),
            "status_category": fields.get("status", {}).get("statusCategory", {}).get("key", ""),
            "resolution": fields.get("resolution", {}).get("name", "") if fields.get("resolution") else "",
            "issuetype": fields.get("issuetype", {}).get("name", ""),
            "priority": fields.get("priority", {}).get("name", "") if fields.get("priority") else "",
            "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
            "created": fields.get("created", ""),
            "updated": fields.get("updated", ""),
        })
    return issues


@router.get("/projects")
def list_projects():
    _require_unlocked()
    config = load_config_decrypted()
    return [
        {"key": p.key, "name": p.name, "board_id": p.board_id}
        for p in config.atlassian.jira_projects
    ]


@router.get("/projects/{project_key}/boards")
def list_boards(project_key: str):
    _require_unlocked()
    atl, _, _ = _get_cache()
    _find_project(atl, project_key)
    try:
        data = atlassian_request(atl, f"/rest/agile/1.0/board?projectKeyOrId={project_key}")
        return [{"id": b["id"], "name": b["name"]} for b in data.get("values", [])]
    except Exception:
        return []


class SetBoardRequest(BaseModel):
    board_id: int


@router.post("/projects/{project_key}/set-board")
def set_board(project_key: str, req: SetBoardRequest):
    _require_unlocked()
    config = load_config_decrypted()
    for p in config.atlassian.jira_projects:
        if p.key.upper() == project_key.upper():
            p.board_id = req.board_id
            save_config(config)
            return {"status": "ok", "key": p.key, "board_id": req.board_id}
    raise HTTPException(status_code=404, detail=f"Project '{project_key}' not configured")


@router.get("/projects/{project_key}/sprint")
def get_sprint(project_key: str, refresh: bool = False, board_id: int | None = None):
    _require_unlocked()
    atl, cache, _ = _get_cache()
    project = _find_project(atl, project_key)

    bid = board_id or project.board_id
    if not bid:
        raise HTTPException(status_code=400, detail=f"No board configured for project '{project_key}'")

    # Always fetch active sprint to resolve current sprint ID (lightweight call)
    sprint_data = atlassian_request(
        atl, f"/rest/agile/1.0/board/{bid}/sprint?state=active"
    )
    sprints = sprint_data.get("values", [])
    if not sprints:
        raise HTTPException(status_code=404, detail="No active sprint found")

    sprint = sprints[0]
    sprint_id = sprint["id"]

    # Cache per sprint ID — a new sprint automatically gets a fresh cache file
    path = cache.sprint_path(project.key, sprint_id)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    # Fetch issues (paginated)
    issues = []
    start_at = 0
    while True:
        issue_data = atlassian_request(
            atl,
            f"/rest/agile/1.0/sprint/{sprint_id}/issue"
            f"?startAt={start_at}&maxResults=100"
            f"&fields=summary,status,issuetype,priority,assignee,created,updated,resolution",
        )
        issues.extend(_build_issue_list(issue_data))
        total = issue_data.get("total", 0)
        start_at += len(issue_data.get("issues", []))
        if start_at >= total:
            break

    result = {
        "sprint": {"id": sprint["id"], "name": sprint["name"], "state": sprint["state"]},
        "issues": issues,
    }
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/projects/{project_key}/sprints")
def list_sprints(project_key: str, refresh: bool = False, board_id: int | None = None):
    _require_unlocked()
    atl, cache, _ = _get_cache()
    project = _find_project(atl, project_key)

    bid = board_id or project.board_id
    if not bid:
        raise HTTPException(status_code=400, detail=f"No board configured for project '{project_key}'")

    path = cache.sprints_list_path(project.key, bid)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    # Fetch all sprints (paginated)
    sprints = []
    start_at = 0
    while True:
        data = atlassian_request(
            atl,
            f"/rest/agile/1.0/board/{bid}/sprint"
            f"?startAt={start_at}&maxResults=50",
        )
        for s in data.get("values", []):
            sprints.append({
                "id": s["id"],
                "name": s.get("name", ""),
                "state": s.get("state", ""),
                "start_date": s.get("startDate", "") or "",
                "end_date": s.get("endDate", "") or "",
                "complete_date": s.get("completeDate", "") or "",
            })
        if data.get("isLast", True):
            break
        start_at += len(data.get("values", []))

    sprints.sort(key=lambda s: s["id"], reverse=True)

    result = {"sprints": sprints, "board_id": bid}
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/projects/{project_key}/sprints/{sprint_id}")
def get_sprint_by_id(project_key: str, sprint_id: int, refresh: bool = False):
    _require_unlocked()
    atl, cache, _ = _get_cache()
    _find_project(atl, project_key)

    path = cache.sprint_path(project_key, sprint_id)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    sprint_info = atlassian_request(atl, f"/rest/agile/1.0/sprint/{sprint_id}")

    issues = []
    start_at = 0
    while True:
        issue_data = atlassian_request(
            atl,
            f"/rest/agile/1.0/sprint/{sprint_id}/issue"
            f"?startAt={start_at}&maxResults=100"
            f"&fields=summary,status,issuetype,priority,assignee,created,updated,resolution",
        )
        issues.extend(_build_issue_list(issue_data))
        total = issue_data.get("total", 0)
        start_at += len(issue_data.get("issues", []))
        if start_at >= total:
            break

    result = {
        "sprint": {
            "id": sprint_info["id"],
            "name": sprint_info.get("name", ""),
            "state": sprint_info.get("state", ""),
            "start_date": sprint_info.get("startDate", "") or "",
            "end_date": sprint_info.get("endDate", "") or "",
            "complete_date": sprint_info.get("completeDate", "") or "",
        },
        "issues": issues,
    }
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/projects/{project_key}/backlog")
def get_backlog(project_key: str, refresh: bool = False, board_id: int | None = None):
    _require_unlocked()
    atl, cache, _ = _get_cache()
    project = _find_project(atl, project_key)

    bid = board_id or project.board_id
    if not bid:
        raise HTTPException(status_code=400, detail=f"No board configured for project '{project_key}'")

    path = cache.backlog_path(project.key, bid)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    issues = []
    start_at = 0
    while True:
        data = atlassian_request(
            atl,
            f"/rest/agile/1.0/board/{bid}/backlog"
            f"?startAt={start_at}&maxResults=100"
            f"&fields=summary,status,issuetype,priority,assignee,created,updated,resolution",
        )
        issues.extend(_build_issue_list(data))
        total = data.get("total", 0)
        start_at += len(data.get("issues", []))
        if start_at >= total:
            break

    result = {"issues": issues}
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/projects/{project_key}/metadata")
def get_project_metadata(project_key: str, refresh: bool = False):
    _require_unlocked()
    atl, cache, _ = _get_cache()
    project = _find_project(atl, project_key)

    path = cache.metadata_path(project.key)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    # Components
    components_raw = atlassian_request(atl, f"/rest/api/2/project/{project.key}/components")
    components = [
        {
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "description": c.get("description", "") or "",
            "lead": c.get("lead", {}).get("displayName", "") if c.get("lead") else "",
        }
        for c in components_raw
    ]

    # Versions
    versions_raw = atlassian_request(atl, f"/rest/api/2/project/{project.key}/versions")
    versions = [
        {
            "id": v.get("id", ""),
            "name": v.get("name", ""),
            "released": v.get("released", False),
            "release_date": v.get("releaseDate", "") or "",
            "archived": v.get("archived", False),
        }
        for v in versions_raw
    ]

    # Issue types + statuses
    statuses_raw = atlassian_request(atl, f"/rest/api/2/project/{project.key}/statuses")
    issue_types = [
        {
            "id": entry.get("id", ""),
            "name": entry.get("name", ""),
            "statuses": [
                {
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "category": s.get("statusCategory", {}).get("name", ""),
                }
                for s in entry.get("statuses", [])
            ],
        }
        for entry in statuses_raw
    ]

    # Priorities (global)
    priorities_raw = atlassian_request(atl, "/rest/api/2/priority")
    priorities = [
        {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "icon_url": p.get("iconUrl", ""),
        }
        for p in priorities_raw
    ]

    # Epics via JQL (paginated)
    epics = []
    start_at = 0
    while True:
        data = _jql_search(
            atl, f"project={project.key} AND issuetype=Epic ORDER BY key ASC",
            "summary,status", start_at,
        )
        for raw in data.get("issues", []):
            fields = raw.get("fields", {})
            epics.append({
                "key": raw["key"],
                "summary": fields.get("summary", ""),
                "status": fields.get("status", {}).get("name", ""),
            })
        total = data.get("total", 0)
        start_at += len(data.get("issues", []))
        if start_at >= total:
            break

    # Labels via JQL (paginated, collect unique)
    labels_set: set[str] = set()
    start_at = 0
    while True:
        data = _jql_search(
            atl, f"project={project.key} AND labels is not EMPTY",
            "labels", start_at,
        )
        for raw in data.get("issues", []):
            for label in raw.get("fields", {}).get("labels", []):
                labels_set.add(label)
        total = data.get("total", 0)
        start_at += len(data.get("issues", []))
        if start_at >= total:
            break

    result = {
        "project_key": project.key,
        "components": components,
        "versions": versions,
        "issue_types": issue_types,
        "priorities": priorities,
        "epics": epics,
        "labels": sorted(labels_set),
    }
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/issues/{issue_key}")
def get_issue(issue_key: str, refresh: bool = False):
    _require_unlocked()
    _, _, svc = _get_cache()

    parts = issue_key.split("-")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Invalid issue key format")

    if refresh:
        result = svc.force_refresh(issue_key)
        result["from_cache"] = False
        return result

    result = svc.get_issue(issue_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_key} not found")
    return result


@router.post("/projects/{project_key}/refresh-issues")
def refresh_issues(project_key: str, request: RefreshIssuesRequest):
    """Batch-refresh full issue details (description, comments, dev-status) for a list of issue keys."""
    _require_unlocked()
    atl, _, svc = _get_cache()
    _find_project(atl, project_key)

    total = len(request.issue_keys)
    refreshed = 0
    errors: list[dict] = []

    for i, key in enumerate(request.issue_keys):
        try:
            svc.get_issue(key)
            refreshed += 1
        except Exception as exc:
            errors.append({"key": key, "error": str(exc)})
        if i < total - 1:
            time.sleep(0.3)

    return {
        "project_key": project_key,
        "issues_total": total,
        "issues_refreshed": refreshed,
        "errors": errors,
    }
