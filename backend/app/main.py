import base64
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.routers import settings, encryption, analysis, jobs, query, browse, jira, confluence, functional

app = FastAPI(title="Roadmap", description="Software project documentation tool")


@app.on_event("startup")
def _auto_unlock():
    """Auto-unlock if ROADMAP_KEY environment variable is set."""
    password = os.environ.get("ROADMAP_KEY")
    if not password:
        return

    from app.config import load_config, save_config, has_encrypted_fields
    from app.crypto import derive_key, decrypt_value, is_encrypted, generate_salt
    from app.session import session

    config = load_config()

    if has_encrypted_fields():
        if not config.encryption_salt:
            print("[STARTUP] ROADMAP_KEY set but config has encrypted "
                  "fields without a salt — skipping auto-unlock",
                  flush=True)
            return
        salt = base64.b64decode(config.encryption_salt)
        key = derive_key(password, salt)
        try:
            if is_encrypted(config.neo4j.password):
                decrypt_value(config.neo4j.password, key)
            else:
                for provider in config.ai_providers:
                    if is_encrypted(provider.api_key):
                        decrypt_value(provider.api_key, key)
                        break
        except Exception:
            print("[STARTUP] ROADMAP_KEY is invalid — auto-unlock failed",
                  flush=True)
            return
        session.set_key(key)
    else:
        salt = generate_salt()
        key = derive_key(password, salt)
        config.encryption_salt = base64.b64encode(salt).decode()
        session.set_key(key)
        save_config(config)

    print("[STARTUP] Auto-unlocked via ROADMAP_KEY", flush=True)

class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

app.add_middleware(NoCacheAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(encryption.router)
app.include_router(analysis.router)
app.include_router(jobs.router)
app.include_router(query.router)
app.include_router(browse.router)
app.include_router(jira.router)
app.include_router(confluence.router)
app.include_router(functional.router)

# --- Embedded frontend (production builds only) ---
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/{full_path:path}")
async def _serve_frontend(full_path: str):
    if not _STATIC_DIR.is_dir():
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    resolved = (_STATIC_DIR / full_path).resolve()
    if full_path and resolved.is_relative_to(_STATIC_DIR) and resolved.is_file():
        return FileResponse(resolved)
    return FileResponse(_STATIC_DIR / "index.html")
