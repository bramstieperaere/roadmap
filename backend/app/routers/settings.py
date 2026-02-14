from fastapi import APIRouter, HTTPException
from neo4j import GraphDatabase

from app.config import load_config_decrypted, save_config, has_encrypted_fields
from app.models import AppConfig
from app.session import session

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
