import asyncio
from collections import Counter

from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_write
from app.config import load_config_decrypted

_BATCH_SIZE = 500

_CYPHER_COLLECT_KEYS_WITH_COMMITS = """
MATCH (:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c:Tooling:Commit)
WHERE size(c.issue_keys) > 0
UNWIND c.issue_keys AS k
RETURN k, c.hash AS hash
"""

_CYPHER_MERGE_TICKETS = """
UNWIND $keys AS k
MERGE (t:Tooling:JiraTicket {key: k})
SET t.project = split(k, '-')[0]
"""

_CYPHER_LINK_REFERENCES = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c:Tooling:Commit)
WHERE size(c.issue_keys) > 0
UNWIND c.issue_keys AS k
MATCH (t:Tooling:JiraTicket {key: k})
MERGE (c)-[:REFERENCES]->(t)
"""

_CYPHER_COUNT_REFERENCES = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c:Tooling:Commit)
      -[:REFERENCES]->(t:Tooling:JiraTicket)
RETURN t.key AS key, count(c) AS commit_count
ORDER BY commit_count DESC
"""

_CYPHER_INDEX_TICKET = """
CREATE INDEX tooling_jira_ticket_key IF NOT EXISTS
FOR (t:JiraTicket) ON (t.key)
"""


def _collect_keys_with_commits(driver, repo_name: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return (unique_keys, {ticket_key: [commit_hashes]})."""
    config = load_config_decrypted()
    ticket_commits: dict[str, list[str]] = {}
    with driver.session(database=config.neo4j.database) as s:
        result = s.run(_CYPHER_COLLECT_KEYS_WITH_COMMITS, {"repo_name": repo_name})
        for record in result:
            key = record["k"]
            h = record["hash"]
            ticket_commits.setdefault(key, []).append(h)
    unique_keys = sorted(ticket_commits.keys())
    return unique_keys, ticket_commits


def _merge_tickets(driver, keys: list[str], job_id: str) -> int:
    total = 0
    for i in range(0, len(keys), _BATCH_SIZE):
        batch = keys[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_MERGE_TICKETS, {"keys": batch})
        total += len(batch)
    return total


def _count_references(driver, repo_name: str) -> list[tuple[str, int]]:
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as s:
        result = s.run(_CYPHER_COUNT_REFERENCES, {"repo_name": repo_name})
        return [(r["key"], r["commit_count"]) for r in result]


async def run_link_jira_tickets(job_id: str, repos: list[dict]):
    """Collect Jira ticket keys from commit issue_keys[], merge stub nodes, create REFERENCES edges."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()

        await asyncio.to_thread(run_cypher_write, driver, _CYPHER_INDEX_TICKET)

        total_tickets = 0
        total_refs = 0

        for repo in repos:
            repo_name = repo["name"]
            job_store.add_log(job_id, "info", f"Processing {repo_name}...")

            keys, ticket_commits = await asyncio.to_thread(
                _collect_keys_with_commits, driver, repo_name)

            if not keys:
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: no issue keys in commits, skipping")
                continue

            # Group by project prefix
            project_counter: Counter[str] = Counter()
            for k in keys:
                project_counter[k.split("-")[0]] += 1
            project_summary = ", ".join(
                f"{proj} ({cnt})" for proj, cnt in project_counter.most_common())
            job_store.add_log(job_id, "info",
                              f"{repo_name}: {len(keys)} unique tickets "
                              f"across {len(project_counter)} projects: {project_summary}")

            # Log individual tickets with their commit counts
            for key in keys:
                commits = ticket_commits[key]
                if len(commits) <= 5:
                    hashes = ", ".join(commits)
                    job_store.add_log(job_id, "info",
                                      f"  {key} -> {len(commits)} commit(s): {hashes}")
                else:
                    first_five = ", ".join(commits[:5])
                    job_store.add_log(job_id, "info",
                                      f"  {key} -> {len(commits)} commit(s): "
                                      f"{first_five} ... (+{len(commits) - 5} more)")

            # Merge ticket nodes
            merged = await asyncio.to_thread(_merge_tickets, driver, keys, job_id)
            job_store.add_log(job_id, "info",
                              f"{repo_name}: merged {merged} JiraTicket nodes")

            # Create REFERENCES edges
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_LINK_REFERENCES,
                {"repo_name": repo_name})

            # Count and log created references
            ref_counts = await asyncio.to_thread(
                _count_references, driver, repo_name)
            total_ref_edges = sum(c for _, c in ref_counts)
            job_store.add_log(job_id, "info",
                              f"{repo_name}: created {total_ref_edges} "
                              f"REFERENCES edges across {len(ref_counts)} tickets")

            total_tickets += merged
            total_refs += total_ref_edges


        job_store.add_log(job_id, "info",
                          f"Done. {total_tickets} ticket nodes, "
                          f"{total_refs} reference edges across "
                          f"{len(repos)} repo(s)")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{total_tickets} tickets, "
                    f"{total_refs} references across {len(repos)} repo(s)")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
