"""MCP server for exposing contexts to AI assistants.

Provides a `get_context` tool that assembles the full content
of a named context from cached Confluence pages and Jira issues.
"""

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from app.config import CONFIG_PATH, load_config_decrypted
from app.jira_cache import JiraCache
from app.analyzers.functional_doc import _clean_confluence_html

mcp = FastMCP("roadmap")


def _render_items(items: list[dict], config, cache) -> list[dict]:
    """Render a list of item dicts into section dicts."""
    sections = []
    for item in items:
        item_type = item.get("type")
        item_id = item.get("id", "")
        label = item.get("label", item.get("title", "Untitled"))

        if item_type == "confluence_page":
            content = _render_confluence_page(cache, config, item_id, label)
        elif item_type == "jira_issue":
            content = _render_jira_issue(cache, item_id, label)
        elif item_type == "instructions":
            content = _render_instructions(label, item.get("text", ""))
        elif item_type == "git_repo":
            content = _render_git_repo(item_id, label, item.get("path", ""))
        elif item_type == "repo_file":
            content = _render_repo_file(item, config)
        else:
            content = f"Unknown item: {item_type} / {item_id}"

        sections.append({
            "type": item_type,
            "id": item_id,
            "label": label,
            "content": content,
        })
    return sections


def render_context_sections(name: str, visited: set[str] | None = None) -> list[dict]:
    """Render each item in a context as a separate section dict.

    Returns a list of {type, id, label, content} dicts.
    Used by both the MCP tool and the preview REST endpoint.

    If name contains '/', it's treated as 'parent/child' and
    returns parent items followed by child items.

    The ``visited`` set prevents infinite recursion when mixins
    reference each other.
    """
    if visited is None:
        visited = set()

    if name in visited:
        return []
    visited.add(name)

    parts = name.split("/", 1)
    parent_name = parts[0]
    child_name = parts[1] if len(parts) > 1 else None

    ctx_path = CONFIG_PATH.parent / "contexts" / f"{parent_name}.json"
    if not ctx_path.exists():
        return []

    from app.routers.contexts import _read_context
    ctx = _read_context(ctx_path)
    config = load_config_decrypted()
    cache = JiraCache(config.atlassian.cache_dir, config.atlassian.refresh_duration)

    parent_items = ctx.get("items", [])

    if child_name:
        child_ctx = next((c for c in ctx.get("children", []) if c["name"] == child_name), None)
        if child_ctx:
            sections: list[dict] = []
            for item in child_ctx.get("items", []):
                if item.get("type") == "parent":
                    # Expand parent items, which may themselves contain mixins
                    for pi in parent_items:
                        if pi.get("type") == "mixin":
                            sections.extend(render_context_sections(pi["id"], visited))
                        else:
                            sections.extend(_render_items([pi], config, cache))
                elif item.get("type") == "mixin":
                    sections.extend(render_context_sections(item["id"], visited))
                else:
                    sections.extend(_render_items([item], config, cache))
            return sections
        return _render_items(parent_items, config, cache)

    # Top-level: render parent items, expanding mixins
    sections = []
    for item in parent_items:
        if item.get("type") == "mixin":
            sections.extend(render_context_sections(item["id"], visited))
        else:
            sections.extend(_render_items([item], config, cache))
    return sections


_TYPE_LABELS = {
    "confluence_page": "Confluence",
    "jira_issue": "Jira",
    "instructions": "Instructions",
    "git_repo": "Git Repo",
    "repo_file": "File",
}


@mcp.tool()
def get_context(name: str) -> str:
    """Get the full content of a named context.

    Returns assembled text from all items in the context
    (Confluence pages, Jira issues, instructions, git repos, repo files).
    """
    sections = render_context_sections(name)
    if not sections:
        ctx_path = CONFIG_PATH.parent / "contexts" / f"{name}.json"
        if not ctx_path.exists():
            return f"Context '{name}' not found."
        return f"Context '{name}' exists but has no items."

    # Table of contents
    n = len(sections)
    first_label = sections[0]["label"]
    toc_lines = [
        f"# Context: {name}",
        "",
        f"This context contains {n} item{'s' if n != 1 else ''}:",
    ]
    for i, s in enumerate(sections, 1):
        type_label = _TYPE_LABELS.get(s["type"], s["type"])
        toc_lines.append(f"{i}. [{type_label}] {s['label']}")
    toc_lines.append("")
    toc_lines.append(
        f"Each item is delimited by ######### <item name> BEGIN ######### "
        f"and ######### <item name> END #########. "
        f"For example: ######### {first_label} BEGIN #########"
    )

    # Sections with delimiters
    parts = []
    for s in sections:
        parts.append(
            f"######### {s['label']} BEGIN #########\n\n"
            f"{s['content']}\n\n"
            f"######### {s['label']} END #########"
        )

    return "\n".join(toc_lines) + "\n\n" + "\n\n".join(parts)


@mcp.tool()
def list_contexts() -> str:
    """List all available context names (including sub-contexts as parent/child)."""
    ctx_dir = CONFIG_PATH.parent / "contexts"
    if not ctx_dir.exists():
        return "No contexts directory found."

    lines = []
    for f in sorted(ctx_dir.glob("*.json")):
        ctx = json.loads(f.read_text(encoding="utf-8"))
        name = ctx.get("name", f.stem)
        lines.append(f"- {name}")
        for child in ctx.get("children", []):
            lines.append(f"  - {name}/{child['name']}")

    if not lines:
        return "No contexts found."

    return "Available contexts:\n" + "\n".join(lines)


def _render_confluence_page(cache: JiraCache, config, page_id: str,
                            label: str) -> str:
    """Render a Confluence page's content as text."""
    # Search across all configured spaces
    for space in config.atlassian.confluence_spaces:
        p = cache.confluence_page_path(space.key, page_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            body_html = data.get("body_html", "")
            clean_text = _clean_confluence_html(body_html)
            return (
                f"## {label} (Confluence Page)\n"
                f"**Space:** {data.get('space_key', space.key)} | "
                f"**Page ID:** {page_id}\n\n"
                f"{clean_text}"
            )

    return f"## {label} (Confluence Page)\n**Page ID:** {page_id}\n\n(Page not found in cache)"


def _render_jira_issue(cache: JiraCache, issue_key: str, label: str) -> str:
    """Render a Jira issue's content as text."""
    parts = issue_key.split("-", 1)
    if len(parts) != 2:
        return f"## {label} ({issue_key})\n\n(Invalid issue key format)"

    project_key = parts[0]
    p = cache.issue_path(project_key, issue_key)
    if not p.exists():
        return f"## {label} ({issue_key})\n\n(Issue not found in cache)"

    data = json.loads(p.read_text(encoding="utf-8"))
    lines = [
        f"## {label} ({issue_key})",
        f"**Status:** {data.get('status', 'Unknown')} | "
        f"**Type:** {data.get('issuetype', 'Unknown')} | "
        f"**Priority:** {data.get('priority', 'Unknown')}",
    ]

    if data.get("assignee"):
        lines.append(f"**Assignee:** {data['assignee']}")

    if data.get("description"):
        lines.append(f"\n### Description\n{data['description']}")

    comments = data.get("comments", [])
    if comments:
        lines.append(f"\n### Comments ({len(comments)})")
        for c in comments[:10]:  # limit to 10 most recent
            lines.append(
                f"\n**{c.get('author', 'Unknown')}** "
                f"({c.get('created', '')[:10]}):\n{c.get('body', '')}"
            )

    return "\n".join(lines)


def _render_instructions(label: str, text: str) -> str:
    """Render free-text instructions."""
    return f"## {label} (Instructions)\n\n{text}"


def _render_git_repo(repo_name: str, label: str, path: str) -> str:
    """Render a git repo reference."""
    return (
        f"## {label} (Git Repository)\n"
        f"**Repository:** {repo_name}\n"
        f"**Checkout path:** {path}"
    )


_MAX_FILE_SIZE = 512 * 1024  # 512 KB

_LANG_MAP = {
    "py": "python", "ts": "typescript", "js": "javascript",
    "java": "java", "yml": "yaml", "yaml": "yaml",
    "json": "json", "xml": "xml", "html": "html", "css": "css",
    "scss": "scss", "md": "markdown", "sh": "bash", "sql": "sql",
    "kt": "kotlin", "rs": "rust", "go": "go", "rb": "ruby",
}


def _render_repo_file(item: dict, config) -> str:
    """Render a single file from a repository with full content."""
    repo_name = item.get("repo_name", "")
    rel_path = item.get("file_path", "")
    label = item.get("label", rel_path)

    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        return f"## {label} (Repository File)\n\n(Repository '{repo_name}' not found)"

    full = Path(repo.path) / rel_path
    if not full.is_file():
        return f"## {label} (Repository File)\n\n(File not found: {rel_path})"

    try:
        size = full.stat().st_size
        if size > _MAX_FILE_SIZE:
            content = full.read_text(encoding="utf-8")[:_MAX_FILE_SIZE]
            content += f"\n\n... (truncated, file is {size:,} bytes)"
        else:
            content = full.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        return f"## {label} (Repository File)\n\n(Cannot read file: {e})"

    ext = full.suffix.lstrip(".")
    lang = _LANG_MAP.get(ext, ext)

    return (
        f"## {label} (Repository File)\n"
        f"**Repository:** {repo_name} | **Path:** {rel_path}\n\n"
        f"```{lang}\n{content}\n```"
    )


def create_mcp_sse_app() -> Starlette:
    """Create a Starlette app that handles MCP SSE connections."""
    sse = SseServerTransport("/messages/")

    class _SseEndpoint:
        """ASGI app wrapper so Starlette doesn't wrap it with request_response."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send):
            async with sse.connect_sse(scope, receive, send) as streams:
                await mcp._mcp_server.run(
                    streams[0], streams[1],
                    mcp._mcp_server.create_initialization_options(),
                )

    routes = [
        Route("/sse", endpoint=_SseEndpoint(), methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]

    return Starlette(routes=routes)
