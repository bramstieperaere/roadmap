from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import settings, encryption, analysis

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

# --- Embedded frontend (production builds only) ---
_STATIC_DIR = Path(__file__).parent / "static"

if _STATIC_DIR.is_dir():

    @app.get("/{full_path:path}")
    async def _serve_frontend(full_path: str):
        file_path = (_STATIC_DIR / full_path).resolve()
        if full_path and file_path.is_relative_to(_STATIC_DIR.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_STATIC_DIR / "index.html")
