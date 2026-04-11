from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import load_config_decrypted
from app.session import session

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}

router = APIRouter(prefix="/api/browse-dir", tags=["browse-dir"])


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


def _check_allowed(path: str):
    """Ensure the path is under the configured scratch base dir or a repo path."""
    config = load_config_decrypted()
    resolved = Path(path).resolve()
    allowed_roots = []
    if config.scratch_base_dir:
        allowed_roots.append(Path(config.scratch_base_dir).resolve())
    for repo in config.repositories:
        if repo.path:
            allowed_roots.append(Path(repo.path).resolve())
    if not allowed_roots:
        raise HTTPException(status_code=403, detail="No browsable directories configured")
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue
    raise HTTPException(status_code=403,
                        detail="Access denied: path is outside allowed directories")


@router.get("")
def list_dir(path: str):
    """List files and directories at the given path."""
    _require_unlocked()
    _check_allowed(path)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            entry = {
                "name": child.name,
                "path": str(child),
                "type": "dir" if child.is_dir() else "file",
            }
            if child.is_file():
                entry["size"] = child.stat().st_size
                entry["ext"] = child.suffix.lower()
            entries.append(entry)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "entries": entries}


@router.get("/viewer-config")
def get_viewer_config(ext: str):
    """Get the viewer config for a file extension."""
    _require_unlocked()
    config = load_config_decrypted()
    for v in config.file_viewers:
        if v.extension.lower() == ext.lower():
            return {"extension": v.extension, "label": v.label,
                    "renderer": v.renderer, "server_url": v.server_url}
    return {"extension": ext, "label": "", "renderer": "text", "server_url": ""}


@router.get("/plantuml-url")
def get_plantuml_url(path: str):
    """Generate a PlantUML server URL for a .puml file."""
    _require_unlocked()
    _check_allowed(path)
    import zlib
    p = Path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = p.read_text(encoding="utf-8", errors="replace")

    # PlantUML custom encoding
    compressed = zlib.compress(content.encode("utf-8"))[2:-4]  # raw deflate
    encoded = _plantuml_encode(compressed)

    config = load_config_decrypted()
    server = ""
    for v in config.file_viewers:
        if v.extension.lower() == ".puml" and v.server_url:
            server = v.server_url.rstrip("/")
            break
    if not server:
        raise HTTPException(status_code=400,
                            detail="No PlantUML server configured. "
                            "Set one up in Settings → File Viewers.")

    return {"svg_url": f"{server}/svg/{encoded}", "png_url": f"{server}/png/{encoded}"}


def _plantuml_encode(data: bytes) -> str:
    """Encode bytes using PlantUML's custom base64 alphabet."""
    res = []
    for i in range(0, len(data), 3):
        if i + 2 < len(data):
            b1, b2, b3 = data[i], data[i + 1], data[i + 2]
            res.append(_encode6bit(b1 >> 2))
            res.append(_encode6bit(((b1 & 0x3) << 4) | (b2 >> 4)))
            res.append(_encode6bit(((b2 & 0xF) << 2) | (b3 >> 6)))
            res.append(_encode6bit(b3 & 0x3F))
        elif i + 1 < len(data):
            b1, b2 = data[i], data[i + 1]
            res.append(_encode6bit(b1 >> 2))
            res.append(_encode6bit(((b1 & 0x3) << 4) | (b2 >> 4)))
            res.append(_encode6bit((b2 & 0xF) << 2))
            res.append("=")
        else:
            b1 = data[i]
            res.append(_encode6bit(b1 >> 2))
            res.append(_encode6bit((b1 & 0x3) << 4))
            res.append("=")
            res.append("=")
    return "".join(res)


def _encode6bit(b: int) -> str:
    if b < 10:
        return chr(48 + b)       # 0-9
    b -= 10
    if b < 26:
        return chr(65 + b)       # A-Z
    b -= 26
    if b < 26:
        return chr(97 + b)       # a-z
    b -= 26
    if b == 0:
        return "-"
    return "_"


@router.get("/read")
def read_file(path: str):
    """Read a text file's content."""
    _require_unlocked()
    _check_allowed(path)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    if p.stat().st_size > 500_000:
        raise HTTPException(status_code=400, detail="File too large (>500KB)")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read: {e}")
    return {"path": str(p), "name": p.name, "ext": p.suffix.lower(), "content": content}


@router.get("/image")
def serve_image(path: str):
    """Serve an image file."""
    _require_unlocked()
    _check_allowed(path)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    if p.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not a supported image type")
    if p.stat().st_size > 10_000_000:
        raise HTTPException(status_code=400, detail="Image too large (>10MB)")
    return FileResponse(p)
