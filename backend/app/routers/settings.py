import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from neo4j import GraphDatabase
from pydantic import BaseModel

from app.config import load_config_decrypted, save_config, has_encrypted_fields
from app.models import AppConfig, JiraProjectConfig
from app.session import session
from app.atlassian_client import atlassian_request, require_atlassian_configured
from app.bitbucket_client import bitbucket_request, require_bitbucket_configured

router = APIRouter(prefix="/api/settings", tags=["settings"])


def require_unlocked():
    if has_encrypted_fields() and not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked. Provide password to unlock.")


@router.get("", response_model=AppConfig)
def get_settings():
    require_unlocked()
    return load_config_decrypted()


@router.put("", response_model=AppConfig)
def update_settings(config: AppConfig):
    require_unlocked()
    # Validate module relative_paths exist on disk
    errors = []
    for repo in config.repositories:
        repo_path = Path(repo.path)
        if not repo_path.is_dir():
            continue  # repo path itself may not exist yet
        for mod in repo.modules:
            module_path = repo_path / mod.relative_path
            if not module_path.is_dir():
                errors.append(
                    f"{repo.name or repo.path}: module "
                    f"'{mod.name}' path '{mod.relative_path}' "
                    f"does not exist")
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    save_config(config)
    return load_config_decrypted()


@router.post("/test-connection")
def test_connection():
    require_unlocked()
    config = load_config_decrypted()
    neo4j = config.neo4j
    try:
        driver = GraphDatabase.driver(neo4j.uri, auth=(neo4j.username, neo4j.password))
        driver.verify_connectivity()
        driver.close()
        return {"status": "ok", "message": "Connected to Neo4j successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/test-atlassian")
def test_atlassian():
    require_unlocked()
    atl = load_config_decrypted().atlassian
    require_atlassian_configured(atl)
    data = atlassian_request(atl, "/rest/api/2/myself")
    name = data.get("displayName", data.get("name", "Unknown"))
    return {"status": "ok", "message": f"Connected as {name}"}


@router.post("/test-bitbucket")
def test_bitbucket():
    require_unlocked()
    atl = load_config_decrypted().atlassian
    require_bitbucket_configured(atl)
    # /user may not work with unified Atlassian tokens; try /workspaces instead
    try:
        data = bitbucket_request(atl, "/user")
        name = data.get("display_name", data.get("nickname", "Unknown"))
        return {"status": "ok", "message": f"Bitbucket: connected as {name}"}
    except HTTPException:
        pass
    # Fallback: list workspaces to verify credentials work at all
    data = bitbucket_request(atl, "/workspaces?pagelen=5")
    names = [w.get("name", w.get("slug", "?")) for w in data.get("values", [])]
    if names:
        return {"status": "ok", "message": f"Bitbucket: access to {', '.join(names)}"}
    return {"status": "ok", "message": "Bitbucket: connected (no workspaces found)"}


@router.get("/atlassian/project")
def lookup_jira_project(key: str):
    require_unlocked()
    atl = load_config_decrypted().atlassian
    require_atlassian_configured(atl)
    project = atlassian_request(atl, f"/rest/api/2/project/{key}")
    try:
        board_data = atlassian_request(atl, f"/rest/agile/1.0/board?projectKeyOrId={key}")
        boards = [{"id": b["id"], "name": b["name"]} for b in board_data.get("values", [])]
    except Exception:
        boards = []
    return {"key": project["key"], "name": project["name"], "boards": boards}


@router.get("/atlassian/confluence-space")
def lookup_confluence_space(key: str):
    require_unlocked()
    atl = load_config_decrypted().atlassian
    require_atlassian_configured(atl)
    prefix = "/wiki" if atl.deployment_type == "cloud" else ""
    data = atlassian_request(atl, f"{prefix}/rest/api/space/{key}")
    return {"key": data["key"], "name": data["name"]}


class BrowseFolderRequest(BaseModel):
    initial_dir: str = ""


class ListSubfoldersRequest(BaseModel):
    path: str


@router.post("/list-subfolders")
def list_subfolders(req: ListSubfoldersRequest):
    p = Path(req.path)
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    folders = sorted(
        entry.name
        for entry in p.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )
    return {"folders": folders}


_FOLDER_PICKER_SCRIPT = '''
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
kwargs = {"title": "Select repository folder"}
if len(sys.argv) > 1 and sys.argv[1]:
    from pathlib import Path
    p = Path(sys.argv[1])
    if p.is_dir():
        kwargs["initialdir"] = str(p)
folder = filedialog.askdirectory(**kwargs)
root.destroy()
print(folder or "")
'''


@router.post("/browse-folder")
async def browse_folder(req: BrowseFolderRequest):
    import subprocess, sys
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _FOLDER_PICKER_SCRIPT, req.initial_dir],
        capture_output=True, text=True, timeout=120,
    )
    path = proc.stdout.strip()
    if not path:
        raise HTTPException(status_code=204, detail="No folder selected")
    return {"path": path}


class VerifyProjectsRequest(BaseModel):
    keys: list[str]


@router.post("/atlassian/verify-projects")
def verify_projects(req: VerifyProjectsRequest):
    require_unlocked()
    atl = load_config_decrypted().atlassian
    require_atlassian_configured(atl)
    projects = []
    for key in req.keys:
        try:
            data = atlassian_request(atl, f"/rest/api/2/project/{key}")
            projects.append({"key": data["key"], "name": data["name"], "valid": True})
        except HTTPException:
            projects.append({"key": key, "name": "", "valid": False})
    return {"projects": projects}


class AddProjectItem(BaseModel):
    key: str
    name: str
    board_id: int | None = None


class AddProjectsRequest(BaseModel):
    projects: list[AddProjectItem]


@router.post("/atlassian/add-projects")
def add_projects(req: AddProjectsRequest):
    require_unlocked()
    config = load_config_decrypted()
    existing_keys = {p.key for p in config.atlassian.jira_projects}
    added = []
    for p in req.projects:
        if p.key not in existing_keys:
            config.atlassian.jira_projects.append(
                JiraProjectConfig(key=p.key, name=p.name, board_id=p.board_id)
            )
            existing_keys.add(p.key)
            added.append(p.key)
    if added:
        save_config(config)
    return {"added": added, "total": len(config.atlassian.jira_projects)}
