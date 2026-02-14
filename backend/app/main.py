from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import settings, encryption, analysis, jobs

app = FastAPI(title="Roadmap", description="Software project documentation tool")

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
