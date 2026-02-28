"""Router for context management.

Contexts are stored as individual JSON files in a 'contexts' directory
next to the config file (sibling of the cache directory).
Each context has a name and a list of items (Confluence pages, Jira issues,
instructions, git repos). Child contexts include a 'parent' placeholder item.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import CONFIG_PATH, load_config_decrypted
from app.jira_cache import JiraCache

router = APIRouter(prefix="/api/contexts", tags=["contexts"])


def _contexts_dir() -> Path:
    d = CONFIG_PATH.parent / "contexts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _context_path(name: str) -> Path:
    return _contexts_dir() / f"{name}.json"


def _read_context(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("items", [])
    data.setdefault("children", [])
    for child in data["children"]:
        child.setdefault("items", [])
        # Ensure every child has a "parent" placeholder item
        if not any(i.get("type") == "parent" for i in child["items"]):
            child["items"].insert(0, {"type": "parent", "id": "parent", "title": "Parent", "label": "Parent"})
    return data


def _get_child(ctx: dict, child_name: str) -> dict:
    for child in ctx["children"]:
        if child["name"] == child_name:
            return child
    raise HTTPException(status_code=404, detail=f"Sub-context '{child_name}' not found")


def _write_context(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_cache() -> JiraCache:
    config = load_config_decrypted()
    atl = config.atlassian
    return JiraCache(atl.cache_dir, atl.refresh_duration)


## ── Mixin helpers ──


def _resolve_mixin(context_path: str) -> dict:
    """Resolve a mixin reference. Validates that the target context exists."""
    parts = context_path.split("/", 1)
    parent_name = parts[0]
    child_name = parts[1] if len(parts) > 1 else None

    ctx_file = _context_path(parent_name)
    if not ctx_file.exists():
        raise HTTPException(status_code=404,
                            detail=f"Mixin target context '{parent_name}' not found")

    if child_name:
        ctx = _read_context(ctx_file)
        if not any(c["name"] == child_name for c in ctx["children"]):
            raise HTTPException(status_code=404,
                                detail=f"Mixin target sub-context '{child_name}' not found in '{parent_name}'")

    title = parts[-1]
    return {
        "type": "mixin",
        "id": context_path,
        "title": title,
        "label": title,
    }


def _detect_cycle(contexts_dir: Path, source_path: str, target_path: str) -> bool:
    """Check if adding a mixin from source_path -> target_path would create a cycle.

    Walks all mixin references reachable from target_path to see if source_path
    is encountered. Also follows parent items (since parent placeholder expands
    parent items which may contain mixins).
    """
    visited: set[str] = set()
    stack = [target_path]

    while stack:
        current = stack.pop()
        if current == source_path:
            return True
        if current in visited:
            continue
        visited.add(current)

        parts = current.split("/", 1)
        parent_name = parts[0]
        child_name = parts[1] if len(parts) > 1 else None

        ctx_file = contexts_dir / f"{parent_name}.json"
        if not ctx_file.exists():
            continue

        try:
            ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        items: list[dict] = ctx.get("items", [])
        if child_name:
            child_ctx = next((c for c in ctx.get("children", []) if c["name"] == child_name), None)
            if child_ctx:
                items = items + child_ctx.get("items", [])

        for item in items:
            if item.get("type") == "mixin":
                stack.append(item["id"])


    return False


def _find_mixin_usages(contexts_dir: Path, context_path: str) -> list[str]:
    """Find all context paths that reference the given path as a mixin."""
    usages = []
    for f in sorted(contexts_dir.glob("*.json")):
        try:
            ctx = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = ctx.get("name", f.stem)
        for item in ctx.get("items", []):
            if item.get("type") == "mixin" and item.get("id") == context_path:
                usages.append(name)
                break
        for child in ctx.get("children", []):
            for item in child.get("items", []):
                if item.get("type") == "mixin" and item.get("id") == context_path:
                    usages.append(f"{name}/{child['name']}")
                    break
    return usages


def _remove_mixin_references(contexts_dir: Path, context_path: str):
    """Remove all mixin items referencing the given context path from all files."""
    for f in contexts_dir.glob("*.json"):
        try:
            ctx = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        modified = False
        orig = len(ctx.get("items", []))
        ctx["items"] = [i for i in ctx.get("items", [])
                        if not (i.get("type") == "mixin" and i.get("id") == context_path)]
        if len(ctx["items"]) != orig:
            modified = True

        for child in ctx.get("children", []):
            orig = len(child.get("items", []))
            child["items"] = [i for i in child.get("items", [])
                              if not (i.get("type") == "mixin" and i.get("id") == context_path)]
            if len(child["items"]) != orig:
                modified = True

        if modified:
            f.write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")


def _update_mixin_references(contexts_dir: Path, old_path: str, new_path: str):
    """Update all mixin items referencing old_path to point to new_path."""
    for f in contexts_dir.glob("*.json"):
        try:
            ctx = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        modified = False
        for item in ctx.get("items", []):
            if item.get("type") == "mixin" and item.get("id") == old_path:
                item["id"] = new_path
                item["title"] = new_path.split("/")[-1]
                item["label"] = new_path.split("/")[-1]
                modified = True

        for child in ctx.get("children", []):
            for item in child.get("items", []):
                if item.get("type") == "mixin" and item.get("id") == old_path:
                    item["id"] = new_path
                    item["title"] = new_path.split("/")[-1]
                    item["label"] = new_path.split("/")[-1]
                    modified = True

        if modified:
            f.write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")


class AddContextRequest(BaseModel):
    name: str


class AddItemRequest(BaseModel):
    type: str  # "confluence_page" | "jira_issue" | "instructions" | "git_repo" | "repo_file" | "mixin"
    id: str
    label: Optional[str] = None
    text: Optional[str] = None  # for instructions


class ItemRef(BaseModel):
    type: str
    id: str


class ReorderRequest(BaseModel):
    items: list[ItemRef]


class MoveItemRequest(BaseModel):
    type: str
    id: str
    from_child: Optional[str] = None  # null = parent, "name" = child
    to_child: Optional[str] = None
    to_index: int = -1  # -1 = append


class RenameContextRequest(BaseModel):
    new_name: str


@router.get("/meta/repositories")
def get_repositories():
    """Return list of configured repositories for the git_repo picker."""
    config = load_config_decrypted()
    return [{"name": r.name, "path": r.path} for r in config.repositories]


_HIDDEN_DIRS = {".git", "node_modules", "__pycache__", ".idea", ".vscode", ".gradle", "target", "build", "dist", ".angular"}


@router.get("/meta/repo-tree/{repo_name}")
def list_repo_tree(repo_name: str, path: str = ""):
    """List files/dirs in a repository subdirectory (one level, for lazy tree)."""
    config = load_config_decrypted()
    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")

    repo_root = Path(repo.path).resolve()
    target = (repo_root / path).resolve() if path else repo_root

    try:
        target.relative_to(repo_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith(".") and child.name in _HIDDEN_DIRS:
                continue
            if child.name in _HIDDEN_DIRS:
                continue
            rel = child.relative_to(repo_root).as_posix()
            entries.append({
                "name": child.name,
                "path": rel,
                "type": "dir" if child.is_dir() else "file",
            })
    except PermissionError:
        pass

    return entries


@router.get("/meta/preview/{name:path}")
def preview_context(name: str):
    """Return preview sections for a context.

    Use 'parent/child' to preview a sub-context (parent items + child items).
    """
    parent_name = name.split("/")[0]
    path = _context_path(parent_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{parent_name}' not found")
    from app.mcp_server import render_context_sections
    return render_context_sections(name)


@router.get("/meta/all-paths")
def get_all_context_paths():
    """Return flat list of all context paths (for mixin picker)."""
    d = _contexts_dir()
    paths = []
    for f in sorted(d.glob("*.json")):
        try:
            ctx = _read_context(f)
            name = ctx["name"]
            paths.append(name)
            for child in ctx.get("children", []):
                paths.append(f"{name}/{child['name']}")
        except Exception:
            continue
    return paths


@router.get("/meta/contributing/{name:path}")
def get_contributing_contexts(name: str):
    """Return context cards that contribute via mixins to a context's output.

    Resolves mixins transitively with cycle protection.  When a mixin
    target is a child context the parent of that child is included too
    (because the child's "parent" placeholder expands into the parent's
    items).  Recursion continues through mixin items found in any
    contributing context.

    Returns [{path, name, items, source: "mixin" | "mixin-parent"}].
    """
    parts = name.split("/", 1)
    parent_name = parts[0]
    child_name = parts[1] if len(parts) > 1 else None

    ctx_file = _context_path(parent_name)
    if not ctx_file.exists():
        raise HTTPException(status_code=404, detail=f"Context '{parent_name}' not found")

    ctx = _read_context(ctx_file)
    contexts_dir = _contexts_dir()
    visited: set[str] = set()
    result: list[dict] = []

    def _collect(items: list[dict]):
        for item in items:
            if item.get("type") != "mixin":
                continue
            mixin_path = item["id"]
            if mixin_path in visited:
                continue
            visited.add(mixin_path)

            mparts = mixin_path.split("/", 1)
            mparent = mparts[0]
            mchild = mparts[1] if len(mparts) > 1 else None

            mfile = contexts_dir / f"{mparent}.json"
            if not mfile.exists():
                continue
            try:
                mctx = _read_context(mfile)
            except Exception:
                continue

            if mchild:
                mchild_ctx = next((c for c in mctx.get("children", []) if c["name"] == mchild), None)
                if mchild_ctx:
                    # The child has a parent placeholder, so the parent
                    # contributes as well.  Add parent first (if not seen).
                    if mparent not in visited:
                        visited.add(mparent)
                        result.append({
                            "path": mparent,
                            "name": mparent,
                            "items": mctx.get("items", []),
                            "source": "mixin-parent",
                        })
                        # Parent's items may contain further mixins
                        _collect(mctx.get("items", []))

                    result.append({
                        "path": mixin_path,
                        "name": mchild,
                        "items": mchild_ctx["items"],
                        "source": "mixin",
                    })
                    # Child's own items may contain further mixins
                    _collect(mchild_ctx.get("items", []))
            else:
                result.append({
                    "path": mixin_path,
                    "name": mparent,
                    "items": mctx.get("items", []),
                    "source": "mixin",
                })
                _collect(mctx.get("items", []))

    visited.add(name)
    if parent_name != name:
        visited.add(parent_name)

    if child_name:
        child_ctx = _get_child(ctx, child_name)
        _collect(ctx.get("items", []))
        _collect(child_ctx.get("items", []))
    else:
        _collect(ctx.get("items", []))

    return result


@router.get("")
def get_contexts():
    d = _contexts_dir()
    contexts = []
    for f in sorted(d.glob("*.json")):
        try:
            contexts.append(_read_context(f))
        except Exception:
            continue
    return contexts


@router.get("/{name}")
def get_context(name: str):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    return _read_context(path)


@router.post("", status_code=201)
def add_context(request: AddContextRequest):
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    path = _context_path(name)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Context '{name}' already exists")

    data = {"name": name, "items": [], "children": []}
    _write_context(path, data)
    return data


@router.delete("/{name}")
def delete_context(name: str, force: bool = False):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    contexts_dir = _contexts_dir()
    ctx = _read_context(path)

    # Collect all paths within this context (parent + children)
    paths_to_check = [name]
    for child in ctx.get("children", []):
        paths_to_check.append(f"{name}/{child['name']}")

    all_usages: list[str] = []
    for cp in paths_to_check:
        all_usages.extend(_find_mixin_usages(contexts_dir, cp))
    all_usages = sorted(set(all_usages))

    if all_usages and not force:
        raise HTTPException(status_code=409, detail={
            "message": f"Context '{name}' is referenced as a mixin by other contexts",
            "usages": all_usages,
        })

    if all_usages:
        for cp in paths_to_check:
            _remove_mixin_references(contexts_dir, cp)

    path.unlink()
    return {"status": "ok"}


@router.put("/{name}/rename")
def rename_context(name: str, request: RenameContextRequest):
    new_name = request.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name is required")
    if new_name == name:
        return _read_context(_context_path(name))

    old_path = _context_path(name)
    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    new_path = _context_path(new_name)
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Context '{new_name}' already exists")

    ctx = _read_context(old_path)
    ctx["name"] = new_name
    _write_context(new_path, ctx)
    old_path.unlink()

    # Update mixin references pointing to old name or old name/child
    contexts_dir = _contexts_dir()
    _update_mixin_references(contexts_dir, name, new_name)
    for child_ctx in ctx["children"]:
        _update_mixin_references(contexts_dir, f"{name}/{child_ctx['name']}", f"{new_name}/{child_ctx['name']}")

    return ctx


@router.post("/{name}/items", status_code=201)
def add_item(name: str, request: AddItemRequest):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    item_type = request.type
    item_id = request.id.strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Item ID is required")

    ctx = _read_context(path)

    # Check for duplicate (instructions can have duplicates since id is auto-generated)
    if item_type != "instructions":
        if any(i["type"] == item_type and i["id"] == item_id for i in ctx["items"]):
            raise HTTPException(status_code=409, detail="Item already in context")

    # Cycle check for mixin items
    if item_type == "mixin":
        if item_id == name:
            raise HTTPException(status_code=400, detail="Cannot mixin a context into itself")
        if _detect_cycle(_contexts_dir(), name, item_id):
            raise HTTPException(status_code=400,
                                detail="Adding this mixin would create a circular reference")

    # Resolve metadata from cache
    item = _resolve_item(item_type, item_id, request.text)

    # Override label if provided
    if request.label is not None:
        item["label"] = request.label

    ctx["items"].append(item)
    _write_context(path, ctx)
    return item


@router.put("/{name}/items/reorder")
def reorder_items(name: str, request: ReorderRequest):
    """Reorder items in a context."""
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    by_key = {(i["type"], i["id"]): i for i in ctx["items"]}
    reordered = []
    for ref in request.items:
        key = (ref.type, ref.id)
        if key in by_key:
            reordered.append(by_key.pop(key))
    # Append any items not mentioned (shouldn't happen, but safe)
    reordered.extend(by_key.values())
    ctx["items"] = reordered
    _write_context(path, ctx)
    return ctx


@router.put("/{name}/items/{item_type}/{item_id:path}")
def update_item(name: str, item_type: str, item_id: str, body: dict):
    """Update an item's mutable fields (label, text)."""
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    for item in ctx["items"]:
        if item["type"] == item_type and item["id"] == item_id:
            if "label" in body:
                item["label"] = body["label"]
            if "text" in body and item_type == "instructions":
                item["text"] = body["text"]
            _write_context(path, ctx)
            return item

    raise HTTPException(status_code=404, detail="Item not found in context")


@router.delete("/{name}/items/{item_type}/{item_id:path}")
def remove_item(name: str, item_type: str, item_id: str):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    before = len(ctx["items"])
    ctx["items"] = [i for i in ctx["items"]
                    if not (i["type"] == item_type and i["id"] == item_id)]
    if len(ctx["items"]) == before:
        raise HTTPException(status_code=404, detail="Item not found in context")

    _write_context(path, ctx)
    return {"status": "ok"}


@router.put("/{name}/items/move")
def move_item(name: str, request: MoveItemRequest):
    """Move an item between parent and child (or between children)."""
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)

    # Resolve source list
    if request.from_child:
        src = _get_child(ctx, request.from_child)["items"]
    else:
        src = ctx["items"]

    # Find and remove item from source
    item = None
    for i, it in enumerate(src):
        if it["type"] == request.type and it["id"] == request.id:
            item = src.pop(i)
            break
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in source")

    # Resolve target list
    if request.to_child:
        dst = _get_child(ctx, request.to_child)["items"]
    else:
        dst = ctx["items"]

    # Insert at index
    if request.to_index < 0 or request.to_index >= len(dst):
        dst.append(item)
    else:
        dst.insert(request.to_index, item)

    _write_context(path, ctx)
    return ctx


## ── Sub-context (children) endpoints ──


@router.post("/{name}/children", status_code=201)
def add_child(name: str, request: AddContextRequest):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    child_name = request.name.strip()
    if not child_name:
        raise HTTPException(status_code=400, detail="Name is required")

    ctx = _read_context(path)
    if any(c["name"] == child_name for c in ctx["children"]):
        raise HTTPException(status_code=409, detail=f"Sub-context '{child_name}' already exists")

    child = {"name": child_name, "items": [
        {"type": "parent", "id": "parent", "title": "Parent", "label": "Parent"},
    ]}
    ctx["children"].append(child)
    _write_context(path, ctx)
    return child


@router.delete("/{name}/children/{child}")
def delete_child(name: str, child: str, force: bool = False):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_path = f"{name}/{child}"
    contexts_dir = _contexts_dir()

    usages = _find_mixin_usages(contexts_dir, child_path)
    if usages and not force:
        raise HTTPException(status_code=409, detail={
            "message": f"Sub-context '{child}' is referenced as a mixin by other contexts",
            "usages": usages,
        })

    before = len(ctx["children"])
    ctx["children"] = [c for c in ctx["children"] if c["name"] != child]
    if len(ctx["children"]) == before:
        raise HTTPException(status_code=404, detail=f"Sub-context '{child}' not found")

    if usages:
        _remove_mixin_references(contexts_dir, child_path)

    _write_context(path, ctx)
    return {"status": "ok"}


@router.put("/{name}/children/{child}/rename")
def rename_child(name: str, child: str, request: RenameContextRequest):
    new_name = request.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name is required")
    if new_name == child:
        path = _context_path(name)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
        return _get_child(_read_context(path), child)

    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_ctx = _get_child(ctx, child)

    if any(c["name"] == new_name for c in ctx["children"]):
        raise HTTPException(status_code=409, detail=f"Sub-context '{new_name}' already exists")

    child_ctx["name"] = new_name
    _write_context(path, ctx)

    # Update mixin references pointing to old child path
    _update_mixin_references(_contexts_dir(), f"{name}/{child}", f"{name}/{new_name}")

    return child_ctx


@router.post("/{name}/children/{child}/items", status_code=201)
def add_child_item(name: str, child: str, request: AddItemRequest):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_ctx = _get_child(ctx, child)

    item_type = request.type
    item_id = request.id.strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Item ID is required")

    if item_type != "instructions":
        if any(i["type"] == item_type and i["id"] == item_id for i in child_ctx["items"]):
            raise HTTPException(status_code=409, detail="Item already in sub-context")

    # Cycle check for mixin items
    if item_type == "mixin":
        source_path = f"{name}/{child}"
        if item_id == source_path or item_id == name:
            raise HTTPException(status_code=400, detail="Cannot mixin a context into itself")
        if _detect_cycle(_contexts_dir(), source_path, item_id):
            raise HTTPException(status_code=400,
                                detail="Adding this mixin would create a circular reference")

    item = _resolve_item(item_type, item_id, request.text)
    if request.label is not None:
        item["label"] = request.label

    child_ctx["items"].append(item)
    _write_context(path, ctx)
    return item


@router.put("/{name}/children/{child}/items/reorder")
def reorder_child_items(name: str, child: str, request: ReorderRequest):
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_ctx = _get_child(ctx, child)

    by_key = {(i["type"], i["id"]): i for i in child_ctx["items"]}
    reordered = []
    for ref in request.items:
        key = (ref.type, ref.id)
        if key in by_key:
            reordered.append(by_key.pop(key))
    reordered.extend(by_key.values())
    child_ctx["items"] = reordered
    _write_context(path, ctx)
    return ctx


@router.put("/{name}/children/{child}/items/{item_type}/{item_id:path}")
def update_child_item(name: str, child: str, item_type: str, item_id: str, body: dict):
    """Update a child item's mutable fields (label, text)."""
    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_ctx = _get_child(ctx, child)
    for item in child_ctx["items"]:
        if item["type"] == item_type and item["id"] == item_id:
            if "label" in body:
                item["label"] = body["label"]
            if "text" in body and item_type == "instructions":
                item["text"] = body["text"]
            _write_context(path, ctx)
            return item

    raise HTTPException(status_code=404, detail="Item not found in sub-context")


@router.delete("/{name}/children/{child}/items/{item_type}/{item_id:path}")
def remove_child_item(name: str, child: str, item_type: str, item_id: str):
    if item_type == "parent":
        raise HTTPException(status_code=400, detail="Cannot remove the parent placeholder item")

    path = _context_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")

    ctx = _read_context(path)
    child_ctx = _get_child(ctx, child)

    before = len(child_ctx["items"])
    child_ctx["items"] = [i for i in child_ctx["items"]
                          if not (i["type"] == item_type and i["id"] == item_id)]
    if len(child_ctx["items"]) == before:
        raise HTTPException(status_code=404, detail="Item not found in sub-context")

    _write_context(path, ctx)
    return {"status": "ok"}


## ── Item resolution ──


def _resolve_item(item_type: str, item_id: str, text: str | None = None) -> dict:
    """Resolve item metadata from the cache."""
    if item_type == "confluence_page":
        return _resolve_confluence_page(item_id)
    elif item_type == "jira_issue":
        return _resolve_jira_issue(item_id)
    elif item_type == "instructions":
        return _resolve_instructions(item_id, text)
    elif item_type == "git_repo":
        return _resolve_git_repo(item_id)
    elif item_type == "repo_file":
        return _resolve_repo_file(item_id)
    elif item_type == "mixin":
        return _resolve_mixin(item_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown item type: {item_type}")


def _resolve_confluence_page(page_id: str) -> dict:
    cache = _get_cache()
    config = load_config_decrypted()

    # Search across all configured spaces
    for space in config.atlassian.confluence_spaces:
        p = cache.confluence_page_path(space.key, page_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            title = data.get("title", "Untitled")
            return {
                "type": "confluence_page",
                "id": page_id,
                "title": title,
                "label": title,
                "space_key": data.get("space_key", space.key),
            }

    raise HTTPException(status_code=404,
                        detail=f"Confluence page {page_id} not found in cache")


def _resolve_jira_issue(issue_key: str) -> dict:
    cache = _get_cache()
    # Extract project key from issue key (e.g. "PROJ-123" -> "PROJ")
    parts = issue_key.split("-", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400,
                            detail=f"Invalid issue key format: {issue_key}")
    project_key = parts[0]

    p = cache.issue_path(project_key, issue_key)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        title = data.get("summary", "Untitled")
        return {
            "type": "jira_issue",
            "id": issue_key,
            "title": title,
            "label": title,
            "project_key": project_key,
        }

    raise HTTPException(status_code=404,
                        detail=f"Jira issue {issue_key} not found in cache")


def _resolve_instructions(item_id: str, text: str | None) -> dict:
    return {
        "type": "instructions",
        "id": item_id,
        "title": "Instructions",
        "label": item_id,
        "text": text or "",
    }


def _resolve_git_repo(repo_name: str) -> dict:
    config = load_config_decrypted()
    for repo in config.repositories:
        if repo.name == repo_name:
            return {
                "type": "git_repo",
                "id": repo_name,
                "title": repo_name,
                "label": repo_name,
                "path": repo.path,
            }

    raise HTTPException(status_code=404,
                        detail=f"Repository '{repo_name}' not found in config")


def _resolve_repo_file(item_id: str) -> dict:
    """Resolve a repo file. item_id format: 'repo_name:relative/path/to/file'."""
    if ":" not in item_id:
        raise HTTPException(status_code=400,
                            detail="repo_file id must be 'repo_name:relative/path'")
    repo_name, rel_path = item_id.split(":", 1)

    config = load_config_decrypted()
    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        raise HTTPException(status_code=404,
                            detail=f"Repository '{repo_name}' not found in config")

    full_path = Path(repo.path) / rel_path
    # Security: ensure resolved path stays within the repo checkout
    try:
        full_path.resolve().relative_to(Path(repo.path).resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    if not full_path.is_file():
        raise HTTPException(status_code=404,
                            detail=f"File '{rel_path}' not found in repository '{repo_name}'")

    filename = full_path.name
    return {
        "type": "repo_file",
        "id": item_id,
        "title": filename,
        "label": rel_path,
        "repo_name": repo_name,
        "file_path": rel_path,
    }
