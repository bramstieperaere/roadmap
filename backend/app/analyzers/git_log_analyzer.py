import asyncio
import json
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

_CYPHER_SET_PROCESSOR_RESULT_TPL = """
UNWIND $items AS item
MATCH (c:Tooling:Commit {{hash: item.hash}})
SET c.{prop} = item.value
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


def _build_all_parents_map(commits: list[dict]) -> dict[str, list[str]]:
    """Build hash -> all parent hashes lookup from parsed commits."""
    ap: dict[str, list[str]] = {}
    for c in commits:
        ap[c["hash"]] = c.get("parent_hashes", [])
    return ap


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


def _walk_all_ancestors(tip_hash: str,
                        all_parents: dict[str, list[str]]) -> list[str]:
    """Walk all reachable commits from tip (BFS), return list of hashes."""
    visited: set[str] = set()
    queue = [tip_hash]
    result = []
    while queue:
        current = queue.pop(0)
        if current in visited or current not in all_parents:
            continue
        visited.add(current)
        result.append(current)
        for parent in all_parents.get(current, []):
            if parent not in visited:
                queue.append(parent)
    return result


def _stamp_branch_on_commits(driver, branch_name: str, repo_name: str,
                             hashes: list[str],
                             first_hash: str | None,
                             job_id: str):
    """Set branches property on all reachable commits and create FIRST link."""
    for i in range(0, len(hashes), _BATCH_SIZE):
        batch = hashes[i:i + _BATCH_SIZE]
        run_cypher_write(driver, _CYPHER_STAMP_BRANCH,
                         {"branch_name": branch_name, "hashes": batch})
    if first_hash:
        run_cypher_write(driver, _CYPHER_BRANCH_FIRST,
                         {"branch_name": branch_name,
                          "repo_name": repo_name,
                          "first_hash": first_hash})
    job_store.add_log(job_id, "info",
                      f"  {branch_name}: {len(hashes)} commits")


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


def _merge_accumulated(accumulated: dict[str, dict], result: dict) -> dict:
    """Merge a processor delta into the accumulated model.

    The accumulated model is keyed by file path. Each file's processor
    output replaces the previous version (latest wins per file).
    Returns the new accumulated state as a flat dict.
    """
    for f in result.get("files", []):
        # Store the full result keyed by file — this captures the latest
        # state of each file as seen by the processor
        accumulated[f] = result
    return accumulated


def _build_accumulated_snapshot(accumulated: dict[str, dict]) -> dict:
    """Build a snapshot of the accumulated model across all files."""
    # Merge all per-file results into a combined view
    all_files = sorted(accumulated.keys())
    if not all_files:
        return {}

    # Collect items from all unique results
    seen_results: list[dict] = []
    seen_ids: set[int] = set()
    for result in accumulated.values():
        rid = id(result)
        if rid not in seen_ids:
            seen_ids.add(rid)
            seen_results.append(result)

    # Merge known list fields across all results
    merged: dict = {"files": all_files}
    list_keys = {
        "changes", "entities", "controllers", "components", "datasources",
        "details", "incoming", "outgoing", "endpoints",
    }
    for result in seen_results:
        for key, value in result.items():
            if key == "files":
                continue
            if key in list_keys and isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif key not in merged:
                merged[key] = value

    # Dedup entities by class name (keep latest)
    if "entities" in merged:
        by_class: dict[str, dict] = {}
        for e in merged["entities"]:
            by_class[e.get("class", "")] = e
        merged["entities"] = list(by_class.values())

    # Dedup controllers by name (keep latest)
    if "controllers" in merged:
        by_name: dict[str, dict] = {}
        for c in merged["controllers"]:
            by_name[c.get("controller", "")] = c
        merged["controllers"] = list(by_name.values())

    # Dedup messaging components by class
    if "components" in merged:
        by_class2: dict[str, dict] = {}
        for c in merged["components"]:
            by_class2[c.get("class", "")] = c
        merged["components"] = list(by_class2.values())

    return merged


def _run_processors(driver, processors, repo_path: str, commits: list[dict],
                    job_id: str,
                    documented: dict[str, set[str]] | None = None,
                    ) -> dict[str, set[str]]:
    """Run commit processors and store results on Neo4j commit nodes.

    Processes commits in chronological order (oldest first) to build
    accumulated models. Stores both delta and accumulated state.

    Returns the documented files mapping (hash -> set of file paths).
    """
    from app.analyzers.commit_processors import CommitProcessor

    # Track documented files per commit across all processors
    if documented is None:
        documented = {}

    # Sort commits oldest-first for accumulation
    sorted_commits = sorted(commits, key=lambda c: c.get("date", ""))

    for proc in processors:
        proc: CommitProcessor
        job_store.add_log(job_id, "info",
                          f"  Running processor: {proc.label}")
        delta_batch: list[dict] = []
        acc_batch: list[dict] = []
        detected_count = 0
        processed_count = 0

        # Per-file accumulation for this processor
        accumulated: dict[str, dict] = {}

        for commit in sorted_commits:
            matched = proc.detect(commit.get("files_changed", []))
            if not matched:
                continue
            detected_count += 1
            job_store.add_log(
                job_id, "info",
                f"    {commit['hash']} - detected {len(matched)} file(s): "
                f"{', '.join(matched[:5])}"
                f"{'...' if len(matched) > 5 else ''}")

            parent_hashes = commit.get("parent_hashes", [])
            parent_full = None
            if parent_hashes:
                # Look up full hash of first parent from commits list
                ph = parent_hashes[0]
                for c2 in sorted_commits:
                    if c2["hash"] == ph:
                        parent_full = c2["full_hash"]
                        break
            result = proc.process(repo_path, commit["full_hash"], matched,
                                  parent_full)
            if result:
                processed_count += 1
                # Track which files this processor documented
                doc_files = result.get("files", [])
                if doc_files:
                    documented.setdefault(
                        commit["hash"], set()).update(doc_files)
                summary_parts = []
                for ch in result.get("changes", []):
                    op = ch.get("op", "?")
                    table = ch.get("table", "")
                    if table:
                        summary_parts.append(f"{op}({table})")
                    else:
                        summary_parts.append(op)
                if summary_parts:
                    job_store.add_log(
                        job_id, "info",
                        f"    {commit['hash']} - processed: "
                        f"{', '.join(summary_parts[:10])}"
                        f"{'...' if len(summary_parts) > 10 else ''}")

                # Store delta
                delta_batch.append({
                    "hash": commit["hash"],
                    "value": json.dumps(result),
                })

                # Update and store accumulated model
                _merge_accumulated(accumulated, result)
                acc_snapshot = _build_accumulated_snapshot(accumulated)
                acc_batch.append({
                    "hash": commit["hash"],
                    "value": json.dumps(acc_snapshot),
                })

        # Batch-write deltas to Neo4j
        if delta_batch:
            cypher = _CYPHER_SET_PROCESSOR_RESULT_TPL.format(
                prop=proc.node_property)
            for i in range(0, len(delta_batch), _BATCH_SIZE):
                chunk = delta_batch[i:i + _BATCH_SIZE]
                run_cypher_write(driver, cypher, {"items": chunk})

        # Batch-write accumulated models
        if acc_batch:
            cypher = _CYPHER_SET_PROCESSOR_RESULT_TPL.format(
                prop=f"{proc.node_property}_acc")
            for i in range(0, len(acc_batch), _BATCH_SIZE):
                chunk = acc_batch[i:i + _BATCH_SIZE]
                run_cypher_write(driver, cypher, {"items": chunk})

        job_store.add_log(
            job_id, "info",
            f"  {proc.label}: {detected_count} commits detected, "
            f"{processed_count} with changes")

        # Update instance_count for incubating processors
        if proc.status == "incubating" and processed_count > 0:
            try:
                from app.config import load_config, save_config
                cfg = load_config()
                for inc in cfg.incubating_processors:
                    if inc.name == proc.name:
                        inc.instance_count += processed_count
                        save_config(cfg)
                        break
            except Exception:
                pass  # non-critical

    # Write documented_files for all commits that had processor output
    if documented:
        doc_batch = [
            {"hash": h, "value": sorted(files)}
            for h, files in documented.items()
        ]
        cypher = _CYPHER_SET_PROCESSOR_RESULT_TPL.format(
            prop="documented_files")
        for i in range(0, len(doc_batch), _BATCH_SIZE):
            chunk = doc_batch[i:i + _BATCH_SIZE]
            run_cypher_write(driver, cypher, {"items": chunk})

        total_files = sum(len(f) for f in documented.values())
        job_store.add_log(
            job_id, "info",
            f"  Documentation coverage: {len(documented)} commits, "
            f"{total_files} files documented")

    return documented


async def run_ingest_commits(job_id: str, repos: list[dict],
                             branch_filter: list[str] | None = None):
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
            git_cmd = ["git", "log"]
            if branch_filter:
                git_cmd += branch_filter
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: filtering by branches: "
                                  f"{', '.join(branch_filter)}")
            else:
                git_cmd.append("--all")
            git_cmd += [f"--format={_GIT_LOG_FORMAT}", "--name-only"]
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    git_cmd,
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
            if branch_filter:
                branches = [b for b in branches if b["name"] in branch_filter]
            if branches:
                await asyncio.to_thread(
                    run_cypher_write, driver, _CYPHER_BRANCHES,
                    {"repo_name": repo_name, "branches": branches})
                # Stamp branch name on all reachable commits
                ap_map = _build_all_parents_map(commits)
                fp_map = _build_first_parent_map(commits)
                for b in branches:
                    all_hashes = _walk_all_ancestors(
                        b["tip_hash"], ap_map)
                    first_chain = _walk_first_parents(
                        b["tip_hash"], fp_map)
                    await asyncio.to_thread(
                        _stamp_branch_on_commits, driver,
                        b["name"], repo_name, all_hashes,
                        first_chain[-1] if first_chain else None,
                        job_id)
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

            # ── Run commit processors ──
            documented: dict[str, set[str]] = {}

            # 1. Matured processors (configured per-repo)
            repo_processor_names = repo.get("processors", [])
            if repo_processor_names:
                from app.analyzers.commit_processors import get_processors_by_name
                matured = get_processors_by_name(repo_processor_names)
                if matured:
                    job_store.add_log(
                        job_id, "info",
                        f"{repo_name}: running matured processors: "
                        f"{', '.join(p.label for p in matured)}")
                    documented = await asyncio.to_thread(
                        _run_processors, driver, matured,
                        repo_path, commits, job_id)

            # 2. Incubating processors (global, from config)
            from app.config import load_config_decrypted
            from app.analyzers.commit_processors import get_incubating_processors
            app_config = load_config_decrypted()
            incubating = get_incubating_processors(app_config)
            if incubating:
                job_store.add_log(
                    job_id, "info",
                    f"{repo_name}: running incubating processors: "
                    f"{', '.join(p.label for p in incubating)}")
                documented = await asyncio.to_thread(
                    _run_processors, driver, incubating,
                    repo_path, commits, job_id, documented)

            # 3. Coverage checker (always last)
            from app.analyzers.commit_processors.coverage_checker import (
                run_coverage_check, suggest_new_processors)
            job_store.add_log(job_id, "info",
                              f"{repo_name}: running coverage check...")
            uncovered_map = run_coverage_check(
                commits, documented, job_id)
            if uncovered_map:
                # Store uncovered_files on commit nodes
                uncov_batch = [
                    {"hash": h, "value": files}
                    for h, files in uncovered_map.items()
                ]
                cypher = _CYPHER_SET_PROCESSOR_RESULT_TPL.format(
                    prop="uncovered_files")
                for i in range(0, len(uncov_batch), _BATCH_SIZE):
                    chunk = uncov_batch[i:i + _BATCH_SIZE]
                    await asyncio.to_thread(
                        run_cypher_write, driver, cypher,
                        {"items": chunk})

                # AI suggestions for new processors
                suggestions = await asyncio.to_thread(
                    suggest_new_processors, uncovered_map, job_id)
                if suggestions:
                    # Store suggestions on the job for review
                    job_store.add_log(
                        job_id, "info",
                        f"{repo_name}: processor suggestions stored "
                        f"in job params")
                    job = job_store.get_job(job_id)
                    if job:
                        existing = job.params.get(
                            "processor_suggestions", {})
                        existing[repo_name] = suggestions
                        job.params["processor_suggestions"] = existing

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
