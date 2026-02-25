from fastapi import APIRouter, HTTPException
from neo4j import GraphDatabase

from app.config import load_config_decrypted, save_config, has_encrypted_fields
from app.models import AppConfig
from app.session import session
from app.atlassian_client import atlassian_request, require_atlassian_configured

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
