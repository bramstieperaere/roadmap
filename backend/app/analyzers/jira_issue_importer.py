import asyncio

from app.config import load_config_decrypted
from app.jira_cache import JiraCache
from app.jira_issue_service import JiraIssueService
from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_write

_BATCH_SIZE = 500

_CYPHER_COLLECT_KEYS = """
MATCH (:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c)
WHERE size(c.issue_keys) > 0
UNWIND c.issue_keys AS k
RETURN DISTINCT k
"""

_CYPHER_CLEAR_PRS = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_ISSUE]->(i:Tooling:Issue)
      -[:HAS_PULL_REQUEST]->(pr:Tooling:PullRequest)
DETACH DELETE pr
"""

_CYPHER_CLEAR_ISSUES = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_ISSUE]->(i:Tooling:Issue)
DETACH DELETE i
"""

_CYPHER_BATCH_ISSUES = """
MATCH (r:Tooling:Repository {name: $repo_name})
UNWIND $issues AS i
MERGE (issue:Tooling:Issue {key: i.key})
SET issue.summary = i.summary,
    issue.status = i.status,
    issue.status_category = i.status_category,
    issue.resolution = i.resolution,
    issue.issuetype = i.issuetype,
    issue.priority = i.priority,
    issue.assignee = i.assignee,
    issue.created = i.created,
    issue.updated = i.updated,
    issue.repo_name = $repo_name
MERGE (r)-[:HAS_ISSUE]->(issue)
"""

_CYPHER_BATCH_PRS = """
UNWIND $prs AS p
MATCH (i:Tooling:Issue {key: p.issue_key})
MERGE (pr:Tooling:PullRequest {url: p.url})
SET pr.name = p.name,
    pr.status = p.status,
    pr.source_branch = p.source_branch,
    pr.destination_branch = p.destination_branch,
    pr.author = p.author
MERGE (i)-[:HAS_PULL_REQUEST]->(pr)
"""

_CYPHER_REFERENCES = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c)
WHERE size(c.issue_keys) > 0
UNWIND c.issue_keys AS k
MATCH (i:Tooling:Issue {key: k})
MERGE (c)-[:REFERENCES]->(i)
"""


def _collect_issue_keys_from_neo4j(driver, repo_name: str) -> set[str]:
    """Query all issue_keys arrays from commits for this repo."""
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as session:
        result = session.run(_CYPHER_COLLECT_KEYS, {"repo_name": repo_name})
        return {record["k"] for record in result}


def _resolve_issues(
    svc: JiraIssueService, issue_keys: set[str], job_id: str,
) -> tuple[list[dict], list[str]]:
    """Resolve issues via cache-then-fetch. Returns (full_issue_dicts, failed_keys)."""
    resolved = []
    failed = []

    for key in sorted(issue_keys):
        data = svc.get_issue(key)
        if data:
            resolved.append(data)
        else:
            failed.append(key)

    job_store.add_log(job_id, "info",
                      f"  {len(resolved)} resolved, {len(failed)} failed")
    return resolved, failed


def _issue_to_flat(data: dict, key: str) -> dict:
    """Pick the flat fields needed for Neo4j Issue nodes."""
    return {
        "key": data.get("key", key),
        "summary": data.get("summary", ""),
        "status": data.get("status", ""),
        "status_category": data.get("status_category", ""),
        "resolution": data.get("resolution", ""),
        "issuetype": data.get("issuetype", ""),
        "priority": data.get("priority", ""),
        "assignee": data.get("assignee", ""),
        "created": data.get("created", ""),
        "updated": data.get("updated", ""),
    }


def _collect_pull_requests(issues: list[dict]) -> list[dict]:
    """Extract PR flat dicts from resolved issues, tagged with issue_key."""
    prs = []
    for issue in issues:
        for pr in issue.get("pull_requests", []):
            if pr.get("url"):
                prs.append({
                    "issue_key": issue["key"],
                    "url": pr["url"],
                    "name": pr.get("name", ""),
                    "status": pr.get("status", ""),
                    "source_branch": pr.get("source_branch", ""),
                    "destination_branch": pr.get("destination_branch", ""),
                    "author": pr.get("author", ""),
                })
    return prs


def _write_prs_batch(
    driver, prs: list[dict], job_id: str,
) -> int:
    """UNWIND batch write PullRequest nodes. Returns total written."""
    total = 0
    for i in range(0, len(prs), _BATCH_SIZE):
        batch = prs[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_BATCH_PRS, {"prs": batch})
        total += len(batch)
        job_store.add_log(job_id, "info",
                          f"  wrote {total}/{len(prs)} pull requests")
    return total


def _write_issues_batch(
    driver, repo_name: str, issues: list[dict], job_id: str,
) -> int:
    """UNWIND batch write Issue nodes. Returns total written."""
    total = 0
    for i in range(0, len(issues), _BATCH_SIZE):
        batch = issues[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_BATCH_ISSUES,
                         {"repo_name": repo_name, "issues": batch})
        total += len(batch)
        job_store.add_log(job_id, "info",
                          f"  wrote {total}/{len(issues)} issues")
    return total


def _write_references(driver, repo_name: str, job_id: str) -> None:
    """Create REFERENCES edges from commits to issues."""
    run_cypher_write(driver, _CYPHER_REFERENCES, {"repo_name": repo_name})
    job_store.add_log(job_id, "info",
                      f"{repo_name}: created REFERENCES relationships")


async def run_import_jira_issues(job_id: str, repos: list[dict]):
    """Main entry point: import Jira issues from cache into Neo4j."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()
        config = load_config_decrypted()
        atl = config.atlassian
        cache = JiraCache(atl.cache_dir if atl else "", atl.refresh_duration if atl else 3600)
        svc = JiraIssueService(atl, cache)

        total_issues_all = 0
        total_prs_all = 0
        total_missing_all = 0

        for repo in repos:
            repo_name = repo["name"]
            job_store.add_log(job_id, "info",
                              f"Processing {repo_name}...")

            # 1. Collect issue keys from commits in Neo4j
            issue_keys = await asyncio.to_thread(
                _collect_issue_keys_from_neo4j, driver, repo_name)
            job_store.add_log(job_id, "info",
                              f"{repo_name}: found {len(issue_keys)} unique "
                              f"issue keys in commits")

            if not issue_keys:
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: no issue keys, skipping")
                continue

            # 2. Resolve issues (cache + live fetch for misses)
            full_issues, failed = await asyncio.to_thread(
                _resolve_issues, svc, issue_keys, job_id)
            if failed:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: failed to resolve: "
                                  f"{', '.join(failed[:20])}"
                                  f"{'...' if len(failed) > 20 else ''}")

            if not full_issues:
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: no issues to import")
                continue

            # 3. Extract flat issues and pull requests
            flat_issues = [_issue_to_flat(d, d["key"]) for d in full_issues]
            prs = _collect_pull_requests(full_issues)

            # 4. Clear existing PR nodes (before clearing issues)
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_CLEAR_PRS,
                {"repo_name": repo_name})

            # 5. Clear existing Issue nodes for this repo
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_CLEAR_ISSUES,
                {"repo_name": repo_name})

            # 6. Batch write Issue nodes
            written = await asyncio.to_thread(
                _write_issues_batch, driver, repo_name, flat_issues, job_id)

            # 7. Batch write PullRequest nodes
            prs_written = 0
            if prs:
                prs_written = await asyncio.to_thread(
                    _write_prs_batch, driver, prs, job_id)
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: {prs_written} pull requests imported")

            # 8. Create REFERENCES relationships
            await asyncio.to_thread(
                _write_references, driver, repo_name, job_id)

            total_issues_all += written
            total_prs_all += prs_written
            total_missing_all += len(failed)

        driver.close()

        job_store.add_log(job_id, "info",
                          f"Done. {total_issues_all} issues imported, "
                          f"{total_prs_all} pull requests imported, "
                          f"{total_missing_all} failed across "
                          f"{len(repos)} repo(s)")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{total_issues_all} issues, "
                    f"{total_prs_all} PRs imported, "
                    f"{total_missing_all} failed")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
