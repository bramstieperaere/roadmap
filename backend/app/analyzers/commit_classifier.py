import asyncio
import json
import subprocess
from pathlib import Path

from openai import OpenAI

from app.config import load_config_decrypted
from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_write

_MAX_COMMITS = 5
_MAX_DIFF_CHARS = 12000

_CATEGORIES = [
    "business-logic",
    "bugfix",
    "config-change",
    "architectural-change",
    "db-change",
    "test",
    "documentation",
    "dependency-update",
    "refactoring",
    "devops",
]

_SYSTEM_PROMPT = f"""You are a commit classifier. Given a git commit message and its diff, classify the commit into one or more categories.

Available categories:
{json.dumps(_CATEGORIES)}

Category definitions:
- business-logic: changes to application/domain logic, feature additions, service implementations
- bugfix: bug fixes, error corrections, null checks, edge case handling
- config-change: application configuration, properties files, feature flags, environment settings
- architectural-change: REST endpoints, JMS listeners/producers, Feign clients, HTTP clients, Spring Data repositories, API contracts
- db-change: database migrations (Liquibase/Flyway), schema changes, SQL scripts, entity mappings
- test: unit tests, integration tests, test fixtures, test configuration
- documentation: README, Javadoc, comments, documentation pages
- dependency-update: pom.xml dependency versions, package.json updates, library upgrades
- refactoring: code restructuring without behavior change, renaming, extracting methods/classes
- devops: CI/CD pipelines, Docker, Kubernetes, build scripts, deployment configuration

Rules:
1. Return a JSON array of category strings, e.g. ["bugfix", "test"]
2. A commit can have multiple categories (e.g. a bugfix that also adds a test)
3. Respond with ONLY the JSON array, no explanation
4. If unsure, pick the closest match(es)
"""

_CYPHER_ENSURE_FACET = """
MERGE (f:Facet:Facet {name: 'CommitType'})
ON CREATE SET f.description = 'AI-classified commit change type'
"""

_CYPHER_ENSURE_VALUE = """
MATCH (f:Facet:Facet {name: 'CommitType'})
MERGE (f)-[:HAS_VALUE]->(v:Facet:Value {name: $name})
ON CREATE SET v.label = $label, v.ordinal = $ordinal
"""

_CYPHER_CLASSIFY = """
MATCH (c:Tooling:Commit {hash: $hash})
MATCH (f:Facet:Facet {name: 'CommitType'})-[:HAS_VALUE]->(v:Facet:Value {name: $tag})
MERGE (c)-[:CLASSIFIED_AS]->(v)
"""

_CYPHER_GET_COMMIT = """
MATCH (c:Tooling:Commit {hash: $hash})
RETURN c.full_hash AS full_hash, c.message AS message,
       c.repo_name AS repo_name, c.files_changed AS files_changed
"""


def _find_provider(config):
    for task_type in ("commit_classification",):
        task = next(
            (t for t in config.ai_tasks if t.task_type == task_type), None)
        if task:
            provider = next(
                (p for p in config.ai_providers
                 if p.name == task.provider_name), None)
            if provider:
                return provider
    if config.ai_providers:
        return config.ai_providers[0]
    return None


def _get_diff(repo_path: str, full_hash: str) -> str:
    """Get the commit diff via git show, truncated to avoid token limits."""
    try:
        proc = subprocess.run(
            ["git", "show", "-U3", "--stat", full_hash],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode == 0:
            diff = proc.stdout or ""
            if len(diff) > _MAX_DIFF_CHARS:
                return diff[:_MAX_DIFF_CHARS] + "\n... (truncated)"
            return diff
    except Exception:
        pass
    return ""


def _classify_commit(client, model: str, message: str, diff: str) -> list[str]:
    """Call AI to classify a single commit."""
    user_content = f"Commit message: {message}\n\nDiff:\n{diff}" if diff else f"Commit message: {message}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        max_tokens=200,
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("["):
        tags = json.loads(text)
        return [t for t in tags if t in _CATEGORIES]
    return []


def _ensure_facet_values(driver):
    """Create the CommitType facet and all category values if they don't exist."""
    run_cypher_write(driver, _CYPHER_ENSURE_FACET)
    for i, cat in enumerate(_CATEGORIES):
        label = cat.replace("-", " ").title()
        run_cypher_write(driver, _CYPHER_ENSURE_VALUE,
                         {"name": cat, "label": label, "ordinal": i})


async def run_classify_commits(job_id: str, commit_hashes: list[str]):
    """Classify commits by type using AI. Limited to _MAX_COMMITS."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        config = load_config_decrypted()
        provider = _find_provider(config)
        if not provider:
            raise Exception("No AI provider configured for commit_classification")

        client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
        driver = get_neo4j_driver()

        # Ensure facet structure exists
        await asyncio.to_thread(_ensure_facet_values, driver)

        hashes = commit_hashes[:_MAX_COMMITS]
        if len(commit_hashes) > _MAX_COMMITS:
            job_store.add_log(job_id, "warn",
                              f"Limited to {_MAX_COMMITS} commits "
                              f"(requested {len(commit_hashes)})")

        # Find repo paths for commits
        repo_paths: dict[str, str] = {}
        for r in config.repositories:
            repo_paths[r.name] = r.path

        total_classified = 0
        for h in hashes:
            # Get commit data from Neo4j
            with driver.session(database=config.neo4j.database) as s:
                record = s.run(_CYPHER_GET_COMMIT, {"hash": h}).single()
            if not record:
                job_store.add_log(job_id, "warn", f"{h}: commit not found in Neo4j")
                continue

            message = record["message"] or ""
            repo_name = record["repo_name"] or ""
            full_hash = record["full_hash"] or h

            # Get diff from git
            repo_path = repo_paths.get(repo_name, "")
            diff = ""
            if repo_path and Path(repo_path).is_dir():
                diff = await asyncio.to_thread(_get_diff, repo_path, full_hash)

            # Classify
            job_store.add_log(job_id, "info", f"{h}: classifying...")
            try:
                tags = await asyncio.to_thread(
                    _classify_commit, client, provider.default_model, message, diff)
            except Exception as e:
                job_store.add_log(job_id, "warn", f"{h}: classification failed: {e}")
                continue

            if not tags:
                job_store.add_log(job_id, "info", f"{h}: no tags matched")
                continue

            # Store classifications
            for tag in tags:
                run_cypher_write(driver, _CYPHER_CLASSIFY, {"hash": h, "tag": tag})

            job_store.add_log(job_id, "info",
                              f"{h}: {', '.join(tags)} — {message[:80]}")
            total_classified += 1


        job_store.add_log(job_id, "info",
                          f"Done. {total_classified}/{len(hashes)} commits classified")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{total_classified} commits classified")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
