import base64

from fastapi import APIRouter, HTTPException

from app.config import load_config, save_config, has_encrypted_fields
from app.crypto import derive_key, decrypt_value, is_encrypted, generate_salt
from app.session import session
from app.models import UnlockRequest, LockStatusResponse

router = APIRouter(prefix="/api/encryption", tags=["encryption"])


@router.get("/status", response_model=LockStatusResponse)
def get_lock_status():
    return LockStatusResponse(
        locked=not session.is_unlocked(),
        has_encrypted_fields=has_encrypted_fields(),
    )


@router.post("/unlock")
def unlock(request: UnlockRequest):
    config = load_config()

    if has_encrypted_fields():
        if not config.encryption_salt:
            raise HTTPException(status_code=400, detail="Config has encrypted fields but no salt")
        salt = base64.b64decode(config.encryption_salt)
        key = derive_key(request.password, salt)
        try:
            if is_encrypted(config.neo4j.password):
                decrypt_value(config.neo4j.password, key)
            else:
                for provider in config.ai_providers:
                    if is_encrypted(provider.api_key):
                        decrypt_value(provider.api_key, key)
                        break
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid password")
        session.set_key(key)
    else:
        salt = generate_salt()
        key = derive_key(request.password, salt)
        config.encryption_salt = base64.b64encode(salt).decode()
        session.set_key(key)
        save_config(config)

    return {"status": "ok", "message": "Unlocked successfully"}


@router.post("/lock")
def lock():
    session.clear_key()
    return {"status": "ok", "message": "Locked successfully"}
