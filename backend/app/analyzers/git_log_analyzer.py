import asyncio
import re
import subprocess
from pathlib import Path

from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_write

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_COMMIT_SEP = "COMMIT_SEP"
_BATCH_SIZE = 500
_GIT_LOG_TIMEOUT = 300

_GIT_LOG_FORMAT = f"{_COMMIT_SEP}%nH:%H%nP:%P%nAN:%an%nAE:%ae%nAI:%aI%nS:%s"

_CYPHER_CLEAR_COMMITS = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_COMMIT]->(c:Tooling:Commit)
DETACH DELETE c
"""

_CYPHER_CLEAR_BRANCHES = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_BRANCH]->(b:Tooling:Branch)
DETACH DELETE b
"""

_CYPHER_REPO = """
MERGE (r:Tooling:Repository {name: $repo_name})
SET r.path = $repo_path
"""

_CYPHER_LINK = """
MATCH (t:Tooling:Repository {name: $repo_name})
MATCH (j:Java:Repository {name: $repo_name})
MERGE (t)-[:SAME_REPO]->(j)
"""

_CYPHER_BATCH = """
MATCH (r:Tooling:Repository {name: $repo_name})
UNWIND $commits AS c
MERGE (commit:Tooling:Commit {hash: c.hash})
SET commit.full_hash = c.full_hash,
    commit.repo_name = $repo_name,
    commit.message = c.message,
    commit.author_name = c.author_name,
    commit.author_email = c.author_email,
    commit.date = c.date,
    commit.issue_keys = c.issue_keys,
    commit.files_changed = c.files_changed
MERGE (r)-[:HAS_COMMIT]->(commit)
"""

_CYPHER_PARENTS = """
UNWIND $edges AS e
MATCH (child:Tooling:Commit {hash: e.child})
MATCH (parent:Tooling:Commit {hash: e.parent})
MERGE (child)-[r:PARENT]->(parent)
SET r.ord = e.ord
"""

_CYPHER_BRANCHES = """
MATCH (r:Tooling:Repository {name: $repo_name})
UNWIND $branches AS b
MERGE (br:Tooling:Branch {name: b.name, repo_name: $repo_name})
MERGE (r)-[:HAS_BRANCH]->(br)
WITH br, b
MATCH (c:Tooling:Commit {hash: b.tip_hash})
MERGE (br)-[:TIP]->(c)
"""

_CYPHER_STAMP_BRANCH = """
UNWIND $hashes AS h
MATCH (c:Tooling:Commit {hash: h})
SET c.branches = CASE
  WHEN c.branches IS NULL THEN [$branch_name]
  WHEN NOT $branch_name IN c.branches THEN c.branches + $branch_name
  ELSE c.branches
END
"""

_CYPHER_BRANCH_FIRST = """
MATCH (br:Tooling:Branch {name: $branch_name, repo_name: $repo_name})
MATCH (c:Tooling:Commit {hash: $first_hash})
MERGE (br)-[:FIRST]->(c)
"""

_CYPHER_INDEX_DATE = """
CREATE INDEX tooling_commit_date IF NOT EXISTS
FOR (c:Commit) ON (c.date)
"""


def _parse_git_log_output(raw: str) -> list[dict]:
    """Parse git log output with prefixed format into commit dicts."""
    commits = []
    blocks = raw.split(_COMMIT_SEP)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        fields = {}
        file_lines = []
        past_subject = False

        for line in block.splitlines():
            if not past_subject:
                if line.startswith("H:"):
                    fields["full_hash"] = line[2:]
                elif line.startswith("P:"):
                    fields["parents"] = line[2:].split()
                elif line.startswith("AN:"):
                    fields["author_name"] = line[3:]
                elif line.startswith("AE:"):
                    fields["author_email"] = line[3:]
                elif line.startswith("AI:"):
                    fields["date"] = line[3:]
                elif line.startswith("S:"):
                    fields["message"] = line[2:]
                    past_subject = True
            else:
                stripped = line.strip()
                if stripped:
                    file_lines.append(stripped)

        if "full_hash" not in fields:
            continue

        full_hash = fields["full_hash"]
        message = fields.get("message", "")
        issue_keys = _JIRA_KEY_RE.findall(message)

        parent_hashes = [p[:12] for p in fields.get("parents", [])]

        commits.append({
            "hash": full_hash[:12],
            "full_hash": full_hash,
            "message": message,
            "author_name": fields.get("author_name", ""),
            "author_email": fields.get("author_email", ""),
            "date": fields.get("date", ""),
            "issue_keys": issue_keys,
            "files_changed": file_lines,
            "parent_hashes": parent_hashes,
        })

    return commits


def _write_commits_batch(driver, repo_name: str, commits: list[dict],
                         job_id: str) -> int:
    """Write commits to Neo4j in batches. Returns total written."""
    total = 0
    for i in range(0, len(commits), _BATCH_SIZE):
        batch = commits[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_BATCH,
                         {"repo_name": repo_name, "commits": batch})
        total += len(batch)
        job_store.add_log(job_id, "info",
                          f"  wrote {total}/{len(commits)} commits")
    return total


def _write_parent_edges(driver, commits: list[dict], job_id: str) -> int:
    """Create PARENT relationships between commits. Returns edge count."""
    edges = []
    for c in commits:
        for i, parent in enumerate(c.get("parent_hashes", [])):
            edges.append({"child": c["hash"], "parent": parent, "ord": i})

    if not edges:
        return 0

    total = 0
    for i in range(0, len(edges), _BATCH_SIZE):
        batch = edges[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_PARENTS, {"edges": batch})
        total += len(batch)

    job_store.add_log(job_id, "info",
                      f"  created {total} PARENT relationships")
    return total


def _build_first_parent_map(commits: list[dict]) -> dict[str, str]:
    """Build hash -> first parent hash lookup from parsed commits."""
    fp = {}
    for c in commits:
        parents = c.get("parent_hashes", [])
        if parents:
            fp[c["hash"]] = parents[0]
    return fp


def _walk_first_parents(tip_hash: str,
                        first_parent: dict[str, str]) -> list[str]:
    """Walk first-parent chain from tip, return list of hashes (tip first)."""
    chain = []
    current = tip_hash
    seen = set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        current = first_parent.get(current)
    return chain


def _stamp_branch_on_commits(driver, branch_name: str, repo_name: str,
                             chain: list[str], job_id: str):
    """Set branches property on commits and create FIRST link."""
    for i in range(0, len(chain), _BATCH_SIZE):
        batch = chain[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_STAMP_BRANCH,
                         {"branch_name": branch_name, "hashes": batch})
    if chain:
        run_cypher_write(driver, _CYPHER_BRANCH_FIRST,
                         {"branch_name": branch_name,
                          "repo_name": repo_name,
                          "first_hash": chain[-1]})
    job_store.add_log(job_id, "info",
                      f"  {branch_name}: {len(chain)} commits")


def _discover_branches(repo_path: str) -> list[dict]:
    """Get local branch names and their tip commit hashes."""
    proc = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short) %(objectname)",
         "refs/heads/"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        return []

    branches = []
    for line in (proc.stdout or "").strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            branches.append({
                "name": parts[0],
                "tip_hash": parts[1][:12],
            })
    return branches


async def run_ingest_commits(job_id: str, repos: list[dict]):
    """Main entry point: ingest git commits into Neo4j for given repos."""
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()

        # Ensure date index exists
        await asyncio.to_thread(
            run_cypher_write, driver, _CYPHER_INDEX_DATE)

        total_commits_all = 0
        total_issues_all = 0
        total_files_all = 0

        for repo in repos:
            repo_name = repo["name"]
            repo_path = repo["path"]
            job_store.add_log(job_id, "info",
                              f"Processing {repo_name}...")

            if not Path(repo_path).is_dir():
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: path does not exist: "
                                  f"{repo_path}")
                continue

            # Run git log
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "log", "--all", f"--format={_GIT_LOG_FORMAT}",
                     "--name-only"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_GIT_LOG_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log timed out after "
                                  f"{_GIT_LOG_TIMEOUT}s")
                continue
            except Exception as e:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log failed: {e}")
                continue

            if proc.returncode != 0:
                job_store.add_log(job_id, "warn",
                                  f"{repo_name}: git log returned exit code "
                                  f"{proc.returncode}: "
                                  f"{(proc.stderr or '').strip()}")
                continue

            # Parse
            commits = _parse_git_log_output(proc.stdout or "")
            job_store.add_log(job_id, "info",
                              f"{repo_name}: parsed {len(commits)} commits")

            if not commits:
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: no commits found, skipping")
                continue

            # Clear existing data for this repo
            job_store.add_log(job_id, "info",
                              f"{repo_name}: clearing existing data...")
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_CLEAR_BRANCHES,
                {"repo_name": repo_name})
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_CLEAR_COMMITS,
                {"repo_name": repo_name})

            # Merge repo node
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_REPO,
                {"repo_name": repo_name, "repo_path": repo_path})

            # Link to Java:Repository (no-op if none exists)
            await asyncio.to_thread(
                run_cypher_write, driver, _CYPHER_LINK,
                {"repo_name": repo_name})

            # Batch write commits
            written = await asyncio.to_thread(
                _write_commits_batch, driver, repo_name, commits, job_id)

            # Create PARENT edges
            parent_count = await asyncio.to_thread(
                _write_parent_edges, driver, commits, job_id)

            # Discover and write branches with membership
            branches = await asyncio.to_thread(
                _discover_branches, repo_path)
            if branches:
                await asyncio.to_thread(
                    run_cypher_write, driver, _CYPHER_BRANCHES,
                    {"repo_name": repo_name, "branches": branches})
                # Stamp branch name on commits via first-parent walk
                fp_map = _build_first_parent_map(commits)
                for b in branches:
                    chain = _walk_first_parents(b["tip_hash"], fp_map)
                    await asyncio.to_thread(
                        _stamp_branch_on_commits, driver,
                        b["name"], repo_name, chain, job_id)
                branch_names = ", ".join(b["name"] for b in branches)
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: {len(branches)} branches "
                                  f"({branch_names})")

            # Stats
            issue_count = sum(len(c["issue_keys"]) for c in commits)
            unique_files = set()
            for c in commits:
                unique_files.update(c["files_changed"])

            total_commits_all += written
            total_issues_all += issue_count
            total_files_all += len(unique_files)

            job_store.add_log(
                job_id, "info",
                f"{repo_name}: {written} commits, "
                f"{issue_count} issue refs, "
                f"{len(unique_files)} unique files")

        driver.close()

        job_store.add_log(job_id, "info",
                          f"Done. {total_commits_all} commits, "
                          f"{total_issues_all} issue refs, "
                          f"{total_files_all} unique files across "
                          f"{len(repos)} repo(s)")
        job_store.update_status(
            job_id, JobStatus.COMPLETED,
            summary=f"{total_commits_all} commits, "
                    f"{total_issues_all} issue refs, "
                    f"{total_files_all} unique files")

    except Exception as e:
        job_store.add_log(job_id, "error", f"Job failed: {e}")
        job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
