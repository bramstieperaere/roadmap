"""MCP server for exposing contexts to AI assistants.

Provides tools for granular context access:
- ``get_context_toc``: lightweight table of contents with byte sizes
- ``get_context_item``: fetch a single item by index
- ``get_context``: fetch everything at once
- ``list_contexts``: discover available context names
"""

import json
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from app.config import CONFIG_PATH, load_config_decrypted
from app.jira_cache import JiraCache
from app.jira_issue_service import JiraIssueService
from app.bitbucket_service import BitbucketService
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
            content = _render_jira_issue(
                JiraIssueService(config.atlassian, cache), item_id, label)
        elif item_type == "instructions":
            content = _render_instructions(label, item.get("text", ""))
        elif item_type == "insight":
            content = _render_insight(label, item.get("text", ""))
        elif item_type == "git_repo":
            content = _render_git_repo(item_id, label, item.get("path", ""))
        elif item_type == "repo_file":
            content = _render_repo_file(item, config)
        elif item_type == "bitbucket_pr":
            content = _render_bitbucket_pr(
                BitbucketService(config.atlassian, cache), item_id, label)
        elif item_type == "commits":
            content = _render_commits(item, config)
        elif item_type == "inquiry":
            content = _render_inquiry(item, config, cache)
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
    "insight": "Agent Insight",
    "git_repo": "Git Repo",
    "repo_file": "File",
    "bitbucket_pr": "Bitbucket",
    "commits": "Commits",
    "inquiry": "Inquiry",
}


def _build_toc(name: str, sections: list[dict], include_hint: bool = True) -> str:
    """Build a table of contents string with byte sizes per item."""
    n = len(sections)
    sizes = [len(s["content"].encode("utf-8")) for s in sections]
    total = sum(sizes)
    lines = [
        f"# Context: {name}",
        "",
        f"Items ({n} total, ~{total:,} bytes):",
    ]
    for i, s in enumerate(sections):
        type_label = _TYPE_LABELS.get(s["type"], s["type"])
        lines.append(f"  {i}. [{type_label}] {s['label']} ({sizes[i]:,} bytes)")
    if include_hint:
        lines.append("")
        lines.append(
            "Use get_context_item(name, item_index) to fetch individual items, "
            "or get_context(name) to fetch everything at once."
        )
    return "\n".join(lines)


@mcp.tool()
def get_context_toc(name: str) -> str:
    """Get a lightweight table of contents for a named context.

    Returns item names, types, and byte sizes without content.
    Use this to decide which items to fetch individually.
    """
    sections = render_context_sections(name)
    if not sections:
        ctx_path = CONFIG_PATH.parent / "contexts" / f"{name}.json"
        if not ctx_path.exists():
            return f"Context '{name}' not found."
        return f"Context '{name}' exists but has no items."

    return _build_toc(name, sections, include_hint=True)


@mcp.tool()
def get_context_item(name: str, item_index: int) -> str:
    """Fetch a single item from a context by its 0-based index.

    Use get_context_toc(name) first to see available indices.
    """
    sections = render_context_sections(name)
    if not sections:
        ctx_path = CONFIG_PATH.parent / "contexts" / f"{name}.json"
        if not ctx_path.exists():
            return f"Context '{name}' not found."
        return f"Context '{name}' exists but has no items."

    if item_index < 0 or item_index >= len(sections):
        return (
            f"Invalid item_index {item_index}. Context '{name}' has "
            f"{len(sections)} items (indices 0-{len(sections) - 1})."
        )

    s = sections[item_index]
    return (
        f"######### {s['label']} BEGIN #########\n\n"
        f"{s['content']}\n\n"
        f"######### {s['label']} END #########"
    )


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

    toc = _build_toc(name, sections, include_hint=False)

    first_label = sections[0]["label"]
    delimiter_hint = (
        f"\nEach item is delimited by ######### <item name> BEGIN ######### "
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

    return toc + delimiter_hint + "\n\n" + "\n\n".join(parts)


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


@mcp.tool()
def add_context_insight(name: str, label: str, text: str) -> str:
    """Write an insight or analysis back to a context as an agent memory item.

    Use this after reading a context to record your analysis, findings,
    or decisions so they are preserved for future reference.

    Args:
        name: context name (e.g. 'my-context' or 'parent/child')
        label: short descriptive title for this insight
        text: the insight content (markdown supported)
    """
    parts = name.split("/", 1)
    parent_name = parts[0]
    child_name = parts[1] if len(parts) > 1 else None

    ctx_path = CONFIG_PATH.parent / "contexts" / f"{parent_name}.json"
    if not ctx_path.exists():
        return f"Context '{name}' not found."

    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))

    import uuid
    item = {
        "type": "insight",
        "id": f"insight-{uuid.uuid4().hex[:8]}",
        "title": "Agent Insight",
        "label": label,
        "text": text,
    }

    if child_name:
        child_ctx = next(
            (c for c in ctx.get("children", []) if c["name"] == child_name),
            None,
        )
        if not child_ctx:
            return f"Sub-context '{child_name}' not found in '{parent_name}'."
        child_ctx.setdefault("items", []).append(item)
    else:
        ctx.setdefault("items", []).append(item)

    ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return f"Insight '{label}' added to context '{name}'."


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


def _render_jira_issue(svc: JiraIssueService, issue_key: str, label: str) -> str:
    """Render a Jira issue's content as text."""
    data = svc.get_issue(issue_key)
    if not data:
        return f"## {label} ({issue_key})\n\n(Issue not found)"
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


def _render_insight(label: str, text: str) -> str:
    """Render an agent insight / memory item."""
    return f"## {label} (Agent Insight)\n\n{text}"


def _render_git_repo(repo_name: str, label: str, path: str) -> str:
    """Render a git repo reference."""
    return (
        f"## {label} (Git Repository)\n"
        f"**Repository:** {repo_name}\n"
        f"**Checkout path:** {path}"
    )


def _render_commits(item: dict, config) -> str:
    """Render a list of commits by their hashes via live git show."""
    repo_name = item.get("repo_name", "")
    hashes = item.get("hashes", [])
    label = item.get("label", item.get("title", "Commits"))

    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        return f"## {label} (Commits)\n\nRepository '{repo_name}' not found in config."

    if not hashes:
        return f"## {label} (Commits)\n\nNo commits specified."

    try:
        proc = subprocess.run(
            ["git", "log", "--no-walk", "--format=%h %aI %an\t%s"] + hashes,
            cwd=repo.path,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except Exception as e:
        return f"## {label} (Commits)\n\nFailed to run git log: {e}"

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        return f"## {label} (Commits)\n\nGit error: {err}"

    lines = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()]
    header = (
        f"## {label} (Commits)\n"
        f"**Repository:** {repo_name}\n"
        f"**Commits:** {len(lines)}\n"
    )

    commits = []
    for line in lines:
        tab_idx = line.find("\t")
        if tab_idx == -1:
            commits.append(f"- {line}")
            continue
        meta, message = line[:tab_idx], line[tab_idx + 1:]
        parts = meta.split(" ", 2)
        if len(parts) >= 3:
            short_hash, date, author = parts[0], parts[1][:10], parts[2]
            commits.append(f"- `{short_hash}` {date} {author} — {message}")
        else:
            commits.append(f"- {line}")

    return header + "\n" + "\n".join(commits)


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


def _render_bitbucket_pr(svc: BitbucketService, pr_url: str, label: str) -> str:
    """Render a Bitbucket PR with comments as text."""
    from app.bitbucket_client import parse_pr_url
    workspace, repo_slug, pr_id = parse_pr_url(pr_url)
    data = svc.get_pr(workspace, repo_slug, pr_id)
    if not data:
        return f"## {label} (Bitbucket PR)\n\n(PR not found)"

    lines = [
        f"## {label} ({workspace}/{repo_slug}#{pr_id})",
        f"**State:** {data.get('state', 'Unknown')} | "
        f"**Author:** {data.get('author', 'Unknown')}",
        f"**Branch:** {data.get('source_branch', '?')} -> {data.get('destination_branch', '?')}",
    ]

    desc = data.get("description", "")
    if desc:
        lines.append(f"\n### Description\n{desc}")

    comments = data.get("comments", [])
    if comments:
        shown = comments[:20]
        lines.append(f"\n### Comments ({len(comments)})")
        for c in shown:
            location = ""
            if c.get("file"):
                location = f" `{c['file']}"
                if c.get("line"):
                    location += f":{c['line']}"
                location += "`"
            lines.append(
                f"\n**{c.get('author', 'Unknown')}** "
                f"({c.get('created', '')[:10]}){location}:\n{c.get('body', '')}"
            )
        if len(comments) > 20:
            lines.append(f"\n... and {len(comments) - 20} more comments")

    return "\n".join(lines)


def _upsert_inquiry(name: str, inquiry_type: str, params: dict, label: str) -> None:
    """Create or update an inquiry item in a context (upsert by type+params)."""
    import uuid
    from datetime import datetime, timezone

    parts = name.split("/", 1)
    parent_name, child_name = parts[0], parts[1] if len(parts) > 1 else None

    ctx_path = CONFIG_PATH.parent / "contexts" / f"{parent_name}.json"
    if not ctx_path.exists():
        return

    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if child_name:
        target = next((c for c in ctx.get("children", []) if c["name"] == child_name), None)
        if not target:
            return
        items_list = target.setdefault("items", [])
    else:
        items_list = ctx.setdefault("items", [])

    existing = next(
        (it for it in items_list
         if it.get("type") == "inquiry"
         and it.get("inquiry_type") == inquiry_type
         and it.get("params", {}) == params),
        None,
    )
    if existing:
        existing["requested_at"] = now
        existing["label"] = label
    else:
        items_list.append({
            "type": "inquiry",
            "id": f"inquiry-{uuid.uuid4().hex[:8]}",
            "title": "Inquiry",
            "label": label,
            "inquiry_type": inquiry_type,
            "params": params,
            "requested_at": now,
        })

    ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")


def _render_git_repo_detail(repo_name: str, label: str, path: str) -> str:
    """Render a git repo with branches and recent commits."""
    lines = [
        f"## {label} (Git Repository)",
        f"**Repository:** {repo_name}",
        f"**Checkout path:** {path}",
    ]
    try:
        proc = subprocess.run(
            ["git", "branch", "-a", "--format=%(refname:short)"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            branches = [b for b in proc.stdout.strip().splitlines() if b][:20]
            if branches:
                lines.append(f"\n**Branches:** {', '.join(branches)}")

        proc = subprocess.run(
            ["git", "log", "--format=%h %aI %an\t%s", "-20"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            commit_lines = [l for l in proc.stdout.strip().splitlines() if l]
            if commit_lines:
                lines.append("\n**Recent commits:**")
                for line in commit_lines:
                    tab = line.find("\t")
                    if tab >= 0:
                        meta, msg = line[:tab], line[tab + 1:]
                        parts = meta.split(" ", 2)
                        if len(parts) >= 3:
                            h, date, author = parts[0], parts[1][:10], parts[2]
                            lines.append(f"- `{h}` {date} {author} — {msg}")
                        else:
                            lines.append(f"- {line}")
    except Exception:
        pass
    return "\n".join(lines)


def _render_inquiry(item: dict, config, cache) -> str:
    """Render an agent inquiry by fetching live data for the referenced resource."""
    inquiry_type = item.get("inquiry_type", "")
    params = item.get("params", {})
    label = item.get("label", "Inquiry")
    requested_at = item.get("requested_at", "")
    header = f"*[Agent inquiry — {requested_at[:10] if requested_at else 'unknown date'}]*\n\n"

    if inquiry_type == "jira_issue":
        issue_key = params.get("issue_key", "")
        return header + _render_jira_issue(JiraIssueService(config.atlassian, cache), issue_key, label)

    if inquiry_type == "confluence_page":
        page_id = params.get("page_id", "")
        return header + _render_confluence_page(cache, config, page_id, label)

    if inquiry_type == "git_repo":
        repo_name = params.get("repo_name", "")
        repo = next((r for r in config.repositories if r.name == repo_name), None)
        if not repo:
            return header + f"## {label}\n\nRepository '{repo_name}' not found in config."
        return header + _render_git_repo_detail(repo_name, label, repo.path)

    if inquiry_type == "git_repos_list":
        lines = [f"## {label} (Available Git Repositories)"]
        for r in config.repositories:
            lines.append(f"- **{r.name}**: `{r.path}`")
        return header + "\n".join(lines)

    return header + f"## {label}\n\nUnknown inquiry type: {inquiry_type}"


@mcp.tool()
def list_git_repos() -> str:
    """List all git repositories available in the roadmap configuration.

    Returns repository names and their local checkout paths.
    Use request_git_repo_info() to get branches and recent commits for a specific repo.
    """
    config = load_config_decrypted()
    if not config.repositories:
        return "No git repositories configured."
    lines = ["Available git repositories:\n"]
    for r in config.repositories:
        lines.append(f"- **{r.name}**: `{r.path}`")
    return "\n".join(lines)


@mcp.tool()
def request_git_repo_info(name: str, repo_name: str, label: str = "") -> str:
    """Fetch branches and recent commits for a git repository.

    Records the inquiry in the context so it is visible in the UI.
    Use list_git_repos() first to discover available repository names.

    Args:
        name:      context name (e.g. 'my-context' or 'parent/child')
        repo_name: repository name as shown by list_git_repos()
        label:     optional display label (defaults to repo name)
    """
    config = load_config_decrypted()
    repo = next((r for r in config.repositories if r.name == repo_name), None)
    if not repo:
        return f"Repository '{repo_name}' not found. Use list_git_repos() to see available names."
    label = label or repo_name
    _upsert_inquiry(name, "git_repo", {"repo_name": repo_name}, label)
    return _render_git_repo_detail(repo_name, label, repo.path)


@mcp.tool()
def request_confluence_page(name: str, page_id: str, label: str = "") -> str:
    """Fetch a Confluence page by its ID and record the inquiry in the context.

    Args:
        name:    context name (e.g. 'my-context' or 'parent/child')
        page_id: numeric Confluence page ID
        label:   optional display label (defaults to 'Page <page_id>')
    """
    config = load_config_decrypted()
    cache = JiraCache(config.atlassian.cache_dir, config.atlassian.refresh_duration)
    label = label or f"Page {page_id}"
    _upsert_inquiry(name, "confluence_page", {"page_id": page_id}, label)
    return _render_confluence_page(cache, config, page_id, label)


@mcp.tool()
def request_jira_issue(name: str, issue_key: str, label: str = "") -> str:
    """Fetch a Jira issue by its key and record the inquiry in the context.

    Args:
        name:      context name (e.g. 'my-context' or 'parent/child')
        issue_key: Jira issue key, e.g. 'TJ-123'
        label:     optional display label (defaults to issue key)
    """
    config = load_config_decrypted()
    cache = JiraCache(config.atlassian.cache_dir, config.atlassian.refresh_duration)
    issue_key = issue_key.upper()
    label = label or issue_key
    _upsert_inquiry(name, "jira_issue", {"issue_key": issue_key}, label)
    return _render_jira_issue(JiraIssueService(config.atlassian, cache), issue_key, label)


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
