import asyncio

from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_write
from app.config import load_config_decrypted

_BATCH_SIZE = 500

_CYPHER_COLLECT_KEYS = """
MATCH (:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c:Tooling:Commit)
WHERE size(c.issue_keys) > 0
UNWIND c.issue_keys AS k
RETURN DISTINCT k
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

_CYPHER_INDEX_TICKET = """
CREATE INDEX tooling_jira_ticket_key IF NOT EXISTS
FOR (t:JiraTicket) ON (t.key)
"""


def _collect_issue_keys(driver, repo_name: str) -> list[str]:
    config = load_config_decrypted()
    with driver.session(database=config.neo4j.database) as s:
        result = s.run(_CYPHER_COLLECT_KEYS, {"repo_name": repo_name})
        return [record["k"] for record in result]


def _merge_tickets(driver, keys: list[str], job_id: str) -> int:
    total = 0
    for i in range(0, len(keys), _BATCH_SIZE):
        batch = keys[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_MERGE_TICKETS, {"keys": batch})
        total += len(batch)
    job_store.add_log(job_id, "info", f"  merged {total} JiraTicket nodes")
    return total


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

            keys = await asyncio.to_thread(_collect_issue_keys, driver, repo_name)
            if not keys:
                job_store.add_log(job_id, "info", f"{repo_name}: no issue keys in commits, skipping")
                continue

            job_store.add_log(job_id, "info",
                              f"{repo_name}: {len(keys)} unique ticket keys found")

            merged = await asyncio.to_thread(_merge_tickets, driver, keys, job_id)

            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_LINK_REFERENCES, {"repo_name": repo_name})
            job_store.add_log(job_id, "info", f"{repo_name}: REFERENCES edges created")

            total_tickets += merged
            total_refs += len(keys)

        driver.close()

        job_store.add_log(job_id, "info",
                          f"Done. {total_tickets} ticket stubs, "
                          f"{total_refs} unique keys across {len(repos)} repo(s)")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{total_tickets} tickets linked across {len(repos)} repo(s)")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
