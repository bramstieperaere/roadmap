"""Bitbucket Cloud PR service with read-through caching.

Fetches PR metadata and comments from Bitbucket Cloud API,
caching results via the existing JiraCache infrastructure.
"""

import logging

from app.bitbucket_client import bitbucket_request, parse_pr_url
from app.jira_cache import JiraCache

log = logging.getLogger(__name__)


def _build_comment(c: dict) -> dict:
    """Normalise a single Bitbucket comment, preserving inline file/line info."""
    inline = c.get("inline", {})
    comment: dict = {
        "author": c.get("user", {}).get("display_name",
                  c.get("user", {}).get("nickname", "")),
        "created": c.get("created_on", ""),
        "body": c.get("content", {}).get("raw", ""),
    }
    if inline.get("path"):
        comment["file"] = inline["path"]
        # "to" is the line the comment is on; "from" is set for moved/deleted lines
        line = inline.get("to") or inline.get("from")
        if line is not None:
            comment["line"] = line
    return comment


def _build_pr_result(pr: dict, comments: list[dict]) -> dict:
    """Normalise a Bitbucket PR + comments into a flat dict."""
    author = pr.get("author", {})
    src = pr.get("source", {}).get("branch", {})
    dst = pr.get("destination", {}).get("branch", {})
    links = pr.get("links", {})
    return {
        "id": pr.get("id"),
        "title": pr.get("title", ""),
        "description": pr.get("description", ""),
        "state": pr.get("state", ""),
        "author": author.get("display_name", author.get("nickname", "")),
        "source_branch": src.get("name", ""),
        "destination_branch": dst.get("name", ""),
        "url": links.get("html", {}).get("href", ""),
        "created_on": pr.get("created_on", ""),
        "updated_on": pr.get("updated_on", ""),
        "comments": [
            _build_comment(c)
            for c in comments
        ],
    }


class BitbucketService:
    def __init__(self, atl, cache: JiraCache):
        self.atl = atl
        self.cache = cache

    def pr_path(self, workspace: str, repo_slug: str, pr_id: int):
        return self.cache._root / "bitbucket" / workspace / repo_slug / "prs" / f"{pr_id}.json"

    def import_pr(self, workspace: str, repo_slug: str, pr_id: int,
                  pr_raw: dict, comments_raw: list[dict]) -> dict:
        """Import PR data from raw API JSON (browser-paste flow)."""
        result = _build_pr_result(pr_raw, comments_raw)
        self.cache.write(self.pr_path(workspace, repo_slug, pr_id), result)
        return result

    def get_pr(self, workspace: str, repo_slug: str, pr_id: int) -> dict | None:
        """Fetch a PR with transparent read-through caching."""
        path = self.pr_path(workspace, repo_slug, pr_id)
        cached = self.cache.read(path)
        if cached is not None:
            return cached

        try:
            pr = bitbucket_request(
                self.atl, f"/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}")
        except Exception:
            log.warning("Failed to fetch PR %s/%s#%s", workspace, repo_slug, pr_id)
            return None

        comments = self._fetch_comments(workspace, repo_slug, pr_id)
        result = _build_pr_result(pr, comments)
        self.cache.write(path, result)
        return result

    def get_pr_from_url(self, url: str) -> dict | None:
        """Parse a Bitbucket PR URL and fetch the PR."""
        workspace, repo_slug, pr_id = parse_pr_url(url)
        return self.get_pr(workspace, repo_slug, pr_id)

    def _fetch_comments(self, workspace: str, repo_slug: str, pr_id: int) -> list[dict]:
        """Fetch all comments for a PR, paginating through results."""
        comments: list[dict] = []
        page_path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments?pagelen=50"
        pages = 0
        while page_path and pages < 10:
            try:
                data = bitbucket_request(self.atl, page_path)
            except Exception:
                break
            comments.extend(data.get("values", []))
            next_url = data.get("next", "")
            if next_url:
                # next_url is absolute; strip the base to get the path
                page_path = next_url.replace("https://api.bitbucket.org/2.0", "")
            else:
                page_path = ""
            pages += 1
        return comments
