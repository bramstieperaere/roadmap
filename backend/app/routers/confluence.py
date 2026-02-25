import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config_decrypted, has_encrypted_fields
from app.session import session
from app.atlassian_client import atlassian_request, require_atlassian_configured
from app.jira_cache import JiraCache

router = APIRouter(prefix="/api/confluence", tags=["confluence"])


def _require_unlocked():
    if has_encrypted_fields() and not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked.")


def _get_cache() -> tuple:
    """Return (atlassian_config, JiraCache)."""
    config = load_config_decrypted()
    atl = config.atlassian
    require_atlassian_configured(atl)
    cache = JiraCache(atl.cache_dir, atl.refresh_duration)
    return atl, cache


def _find_space(atl, space_key: str):
    for s in atl.confluence_spaces:
        if s.key.upper() == space_key.upper():
            return s
    raise HTTPException(status_code=404, detail=f"Space '{space_key}' not configured")


def _wiki_prefix(atl) -> str:
    return "/wiki" if atl.deployment_type == "cloud" else ""


def _build_page_tree(pages_flat: list) -> list:
    """Build a nested tree from a flat list of pages with parent_id."""
    by_id = {p["id"]: p for p in pages_flat}
    roots = []
    for page in pages_flat:
        parent_id = page.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(page)
        else:
            roots.append(page)
    return roots


@router.get("/spaces")
def list_spaces():
    _require_unlocked()
    config = load_config_decrypted()
    return [
        {"key": s.key, "name": s.name}
        for s in config.atlassian.confluence_spaces
    ]


def _fetch_pages_flat(atl, space_key: str) -> list:
    """Fetch all pages in a space as a flat list."""
    pages_flat = []
    start = 0
    limit = 200  # Confluence Cloud caps at 200 regardless of requested limit
    prefix = _wiki_prefix(atl)
    while True:
        data = atlassian_request(
            atl,
            f"{prefix}/rest/api/content"
            f"?spaceKey={space_key}&type=page&expand=ancestors,version"
            f"&start={start}&limit={limit}",
        )
        for page in data.get("results", []):
            ancestors = page.get("ancestors", [])
            parent_id = ancestors[-1]["id"] if ancestors else None
            pages_flat.append({
                "id": page["id"],
                "title": page.get("title", ""),
                "parent_id": parent_id,
                "version": page.get("version", {}).get("number", 1),
                "children": [],
            })
        # Use _links.next to determine if there are more pages;
        # comparing size < limit is unreliable because the server
        # may silently cap the limit (Cloud caps at 200).
        if "next" not in data.get("_links", {}):
            break
        size = data.get("size", len(data.get("results", [])))
        start += size
    return pages_flat


def _fetch_single_page(atl, page_id: str) -> dict:
    """Fetch a single page's content from the API."""
    prefix = _wiki_prefix(atl)
    data = atlassian_request(
        atl,
        f"{prefix}/rest/api/content/{page_id}"
        f"?expand=body.storage,version,ancestors,space",
    )
    ancestors = data.get("ancestors", [])
    return {
        "id": data["id"],
        "title": data.get("title", ""),
        "space_key": data.get("space", {}).get("key", "unknown"),
        "body_html": data.get("body", {}).get("storage", {}).get("value", ""),
        "version": data.get("version", {}).get("number", 1),
        "version_by": data.get("version", {}).get("by", {}).get("displayName", ""),
        "version_when": data.get("version", {}).get("when", ""),
        "ancestors": [
            {"id": a["id"], "title": a.get("title", "")}
            for a in ancestors
        ],
    }


@router.get("/spaces/{space_key}/pages")
def get_pages(space_key: str, refresh: bool = False):
    _require_unlocked()
    atl, cache = _get_cache()
    space = _find_space(atl, space_key)

    path = cache.confluence_pages_path(space.key)
    if not refresh:
        cached = cache.read(path)
        if cached:
            cached["from_cache"] = True
            return cached

    pages_flat = _fetch_pages_flat(atl, space.key)
    tree = _build_page_tree(pages_flat)

    result = {"space_key": space.key, "pages": tree, "total": len(pages_flat)}
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.get("/pages/{page_id}")
def get_page(page_id: str, refresh: bool = False):
    _require_unlocked()
    atl, cache = _get_cache()

    # Check all configured space caches first
    if not refresh:
        for space in atl.confluence_spaces:
            p = cache.confluence_page_path(space.key, page_id)
            cached = cache.read(p)
            if cached:
                cached["from_cache"] = True
                return cached

    result = _fetch_single_page(atl, page_id)
    path = cache.confluence_page_path(result["space_key"], page_id)
    cache.write(path, result)
    result["from_cache"] = False
    return result


@router.post("/spaces/{space_key}/refresh")
def refresh_space(space_key: str):
    """Refresh the page tree and all page contents for a space, throttled to 5 req/s."""
    _require_unlocked()
    atl, cache = _get_cache()
    space = _find_space(atl, space_key)

    # 1. Refresh page tree
    pages_flat = _fetch_pages_flat(atl, space.key)
    tree = _build_page_tree([{**p, "children": []} for p in pages_flat])
    tree_result = {"space_key": space.key, "pages": tree, "total": len(pages_flat)}
    cache.write(cache.confluence_pages_path(space.key), tree_result)

    # 2. Fetch each page content, throttled at 5 req/s (200ms between requests)
    refreshed = 0
    errors = []
    for page in pages_flat:
        try:
            result = _fetch_single_page(atl, page["id"])
            cache.write(cache.confluence_page_path(space.key, page["id"]), result)
            refreshed += 1
        except Exception as e:
            errors.append({"id": page["id"], "title": page["title"], "error": str(e)})
        time.sleep(0.2)

    return {
        "space_key": space.key,
        "pages_total": len(pages_flat),
        "pages_refreshed": refreshed,
        "errors": errors,
    }


class RefreshPagesRequest(BaseModel):
    page_ids: list[str]


@router.post("/spaces/{space_key}/refresh-pages")
def refresh_pages(space_key: str, request: RefreshPagesRequest):
    """Refresh specific pages by ID, throttled to 5 req/s."""
    _require_unlocked()
    atl, cache = _get_cache()
    space = _find_space(atl, space_key)

    refreshed = 0
    errors = []
    for page_id in request.page_ids:
        try:
            result = _fetch_single_page(atl, page_id)
            cache.write(cache.confluence_page_path(space.key, page_id), result)
            refreshed += 1
        except Exception as e:
            errors.append({"id": page_id, "error": str(e)})
        time.sleep(0.2)

    return {
        "space_key": space.key,
        "pages_total": len(request.page_ids),
        "pages_refreshed": refreshed,
        "errors": errors,
    }
