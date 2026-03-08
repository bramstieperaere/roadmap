"""Unified Jira issue service with read-through caching.

Single source of truth for fetching individual Jira issues.
Cache hit = return immediately. Cache miss = fetch from Jira, cache, return.
Done issues never expire (smart freshness via JiraCache.read_issue).
"""

import logging

from app.atlassian_client import atlassian_request
from app.jira_cache import JiraCache

log = logging.getLogger(__name__)

_APP_TYPES = ("stash", "bitbucket", "github", "gitlab")


def _build_issue_result(raw: dict) -> dict:
    """Extract a normalised dict from a Jira REST /issue/ response."""
    fields = raw.get("fields", {})
    return {
        "key": raw["key"],
        "summary": fields.get("summary", ""),
        "status": fields.get("status", {}).get("name", ""),
        "status_category": fields.get("status", {}).get("statusCategory", {}).get("key", ""),
        "resolution": fields.get("resolution", {}).get("name", "") if fields.get("resolution") else "",
        "issuetype": fields.get("issuetype", {}).get("name", ""),
        "priority": fields.get("priority", {}).get("name", "") if fields.get("priority") else "",
        "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
        "reporter": fields.get("reporter", {}).get("displayName", "") if fields.get("reporter") else "",
        "created": fields.get("created", ""),
        "updated": fields.get("updated", ""),
        "description": fields.get("description", ""),
        "labels": fields.get("labels", []),
        "components": [c.get("name", "") for c in fields.get("components", [])],
        "fix_versions": [v.get("name", "") for v in fields.get("fixVersions", [])],
        "subtasks": [
            {"key": s["key"], "summary": s.get("fields", {}).get("summary", ""),
             "status": s.get("fields", {}).get("status", {}).get("name", "")}
            for s in fields.get("subtasks", [])
        ],
        "issuelinks": [
            {
                "type": link.get("type", {}).get("name", ""),
                "direction": "outward" if "outwardIssue" in link else "inward",
                "key": (link.get("outwardIssue") or link.get("inwardIssue", {})).get("key", ""),
                "summary": (link.get("outwardIssue") or link.get("inwardIssue", {})).get("fields", {}).get("summary", ""),
            }
            for link in fields.get("issuelinks", [])
        ],
        "comment_count": fields.get("comment", {}).get("total", 0),
        "comments": [
            {
                "author": c.get("author", {}).get("displayName", ""),
                "created": c.get("created", ""),
                "body": c.get("body", ""),
            }
            for c in fields.get("comment", {}).get("comments", [])
        ],
        "branches": [],
        "commits": [],
        "pull_requests": [],
    }


class JiraIssueService:
    def __init__(self, atl, cache: JiraCache):
        self.atl = atl
        self.cache = cache

    def get_issue(self, issue_key: str, *,
                  include_dev_status: bool = True) -> dict | None:
        """Fetch an issue with transparent read-through caching.

        Returns the cached result on hit, fetches from Jira on miss,
        or None if the fetch fails.
        """
        parts = issue_key.split("-")
        if len(parts) < 2:
            return None
        project_key = parts[0]

        path = self.cache.issue_path(project_key, issue_key)
        cached = self.cache.read_issue(path)
        if cached is not None:
            return cached

        try:
            raw = atlassian_request(self.atl, f"/rest/api/2/issue/{issue_key}")
        except Exception:
            log.warning("Failed to fetch issue %s from Jira", issue_key)
            return None

        result = _build_issue_result(raw)

        if include_dev_status:
            issue_id = raw.get("id", "")
            if issue_id:
                self._fetch_dev_status(issue_id, result)

        self.cache.write(path, result)
        return result

    def force_refresh(self, issue_key: str) -> dict:
        """Always fetch from Jira, cache, and return. Raises on failure."""
        parts = issue_key.split("-")
        if len(parts) < 2:
            raise ValueError(f"Invalid issue key: {issue_key}")
        project_key = parts[0]

        raw = atlassian_request(self.atl, f"/rest/api/2/issue/{issue_key}")
        result = _build_issue_result(raw)

        issue_id = raw.get("id", "")
        if issue_id:
            self._fetch_dev_status(issue_id, result)

        path = self.cache.issue_path(project_key, issue_key)
        self.cache.write(path, result)
        return result

    def _fetch_dev_status(self, issue_id: str, result: dict) -> None:
        """Fetch branches, commits and PRs from Jira dev-status API."""
        base = "/rest/dev-status/latest/issue/detail"
        for app_type in _APP_TYPES:
            try:
                data = atlassian_request(
                    self.atl, f"{base}?issueId={issue_id}&applicationType={app_type}&dataType=branch",
                )
                for detail in data.get("detail", []):
                    for branch in detail.get("branches", []):
                        result["branches"].append({
                            "name": branch.get("name", ""),
                            "url": branch.get("url", ""),
                            "repository": branch.get("repository", {}).get("name", ""),
                        })
            except Exception:
                pass
            try:
                data = atlassian_request(
                    self.atl, f"{base}?issueId={issue_id}&applicationType={app_type}&dataType=repository",
                )
                for detail in data.get("detail", []):
                    for repo in detail.get("repositories", []):
                        rn = repo.get("name", "")
                        for commit in repo.get("commits", []):
                            result["commits"].append({
                                "id": commit.get("id", "")[:12],
                                "message": commit.get("message", ""),
                                "author": commit.get("author", {}).get("name", ""),
                                "url": commit.get("url", ""),
                                "repository": rn,
                                "timestamp": commit.get("authorTimestamp", ""),
                            })
            except Exception:
                pass
            try:
                data = atlassian_request(
                    self.atl, f"{base}?issueId={issue_id}&applicationType={app_type}&dataType=pullrequest",
                )
                for detail in data.get("detail", []):
                    for pr in detail.get("pullRequests", []):
                        result["pull_requests"].append({
                            "id": pr.get("id", ""),
                            "name": pr.get("name", ""),
                            "status": pr.get("status", ""),
                            "url": pr.get("url", ""),
                            "source_branch": pr.get("source", {}).get("branch", ""),
                            "destination_branch": pr.get("destination", {}).get("branch", ""),
                            "author": pr.get("author", {}).get("name", ""),
                        })
            except Exception:
                pass
