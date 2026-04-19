import asyncio
import json
import re
import subprocess
from pathlib import Path

from datetime import datetime, timezone

from app.job_store import job_store
from app.models_jobs import JobStatus
from app.neo4j_client import get_neo4j_driver, run_cypher_read, run_cypher_write

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_COMMIT_SEP = "COMMIT_SEP"
_BATCH_SIZE = 500
_GIT_LOG_TIMEOUT = 300
_COMMIT_CHUNK_SIZE = None  # set to an int to limit commits per run (for testing)

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

_CYPHER_GET_INGESTION_STATE = """
MATCH (r:Tooling:Repository {name: $repo_name})
RETURN r.ingestion_state AS state
"""

_CYPHER_SET_INGESTION_STATE = """
MATCH (r:Tooling:Repository {name: $repo_name})
SET r.ingestion_state = $state
"""

_CYPHER_SET_COMMIT_PROC_VERSIONS = """
UNWIND $items AS item
MATCH (c:Tooling:Commit {hash: item.hash})
SET c.processor_versions = item.versions
"""

_CYPHER_GET_LATEST_ACC = """
MATCH (r:Tooling:Repository {{name: $repo_name}})-[:HAS_COMMIT]->(c:Tooling:Commit)
WHERE c.{prop} IS NOT NULL
RETURN c.{prop} AS acc
ORDER BY c.date DESC
LIMIT 1
"""

_CYPHER_REMOVE_STALE_BRANCH = """
MATCH (r:Tooling:Repository {name: $repo_name})-[:HAS_BRANCH]->(b:Tooling:Branch {name: $branch_name})
DETACH DELETE b
"""

_CYPHER_UNSTAMP_BRANCH = """
MATCH (c:Tooling:Commit)
WHERE c.repo_name = $repo_name AND $branch_name IN c.branches
SET c.branches = [b IN c.branches WHERE b <> $branch_name]
"""


def _load_ingestion_state(driver, repo_name: str) -> dict | None:
    """Read ingestion state JSON from the Repository node."""
    rows = run_cypher_read(driver, _CYPHER_GET_INGESTION_STATE,
                           {"repo_name": repo_name})
    if rows and rows[0].get("state"):
        return json.loads(rows[0]["state"])
    return None


def _save_ingestion_state(driver, repo_name: str, state: dict):
    """Write ingestion state JSON to the Repository node."""
    run_cypher_write(driver, _CYPHER_SET_INGESTION_STATE,
                     {"repo_name": repo_name,
                      "state": json.dumps(state)})


def _build_incremental_git_cmd(branch_filter: list[str] | None,
                               prior_tips: dict[str, str]) -> list[str]:
    """Build git log args that fetch only commits beyond prior tips.

    Uses origin/ remote tracking refs and git's ^hash exclusion to skip
    commits reachable from any previously-known branch tip.
    """
    cmd = ["git", "log"]
    if branch_filter:
        cmd += [f"origin/{b}" for b in branch_filter]
    else:
        cmd.append("--all")
    for tip_hash in prior_tips.values():
        cmd.append(f"^{tip_hash}")
    cmd += [f"--format={_GIT_LOG_FORMAT}", "--name-only"]
    return cmd


def _load_prior_accumulated(driver, repo_name: str,
                            processors) -> dict[str, dict]:
    """Load the latest accumulated model for each processor from Neo4j.

    Returns {node_property: accumulated_dict}.
    """
    result: dict[str, dict] = {}
    for proc in processors:
        prop = f"{proc.node_property}_acc"
        cypher = _CYPHER_GET_LATEST_ACC.format(prop=prop)
        rows = run_cypher_read(driver, cypher, {"repo_name": repo_name})
        if rows and rows[0].get("acc"):
            try:
                acc = json.loads(rows[0]["acc"])
                # Rebuild per-file mapping from the snapshot
                per_file: dict[str, dict] = {}
                for f in acc.get("files", []):
                    per_file[f] = acc
                result[proc.node_property] = per_file
            except (json.JSONDecodeError, TypeError):
                pass
    return result


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


def _fetch_remote_branches(repo_path: str,
                           branches: list[str] | None = None,
                           job_id: str | None = None) -> bool:
    """Fetch remote tracking refs without touching the working tree.

    If branches is given, fetches only those branches. Otherwise fetches all.
    Returns True on success.
    """
    cmd = ["git", "fetch", "origin"]
    if branches:
        cmd += branches
    else:
        cmd.append("--all")
    try:
        proc = subprocess.run(
            cmd, cwd=repo_path,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
        if proc.returncode != 0 and job_id:
            job_store.add_log(job_id, "warn",
                              f"git fetch failed: {(proc.stderr or '').strip()}")
        return proc.returncode == 0
    except Exception as e:
        if job_id:
            job_store.add_log(job_id, "warn", f"git fetch failed: {e}")
        return False


def _discover_branches(repo_path: str,
                       use_remote: bool = True) -> list[dict]:
    """Get branch names and their tip commit hashes.

    When use_remote=True (default), reads from origin remote tracking refs.
    This avoids depending on which branch is checked out locally.
    """
    ref_prefix = "refs/remotes/origin/" if use_remote else "refs/heads/"
    proc = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short) %(objectname)",
         ref_prefix],
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
            name = parts[0]
            # Strip "origin/" prefix from remote tracking refs
            if use_remote and name.startswith("origin/"):
                name = name[len("origin/"):]
            if name == "HEAD":
                continue
            branches.append({
                "name": name,
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
                    prior_accumulated: dict[str, dict] | None = None,
                    ) -> dict[str, set[str]]:
    """Run commit processors and store results on Neo4j commit nodes.

    Processes commits in chronological order (oldest first) to build
    accumulated models. Stores both delta and accumulated state.

    Args:
        prior_accumulated: Optional {node_property: per_file_dict} to seed
            accumulation from a previous incremental run.

    Returns the documented files mapping (hash -> set of file paths).
    """
    from app.analyzers.commit_processors import CommitProcessor

    # Track documented files per commit across all processors
    if documented is None:
        documented = {}
    if prior_accumulated is None:
        prior_accumulated = {}

    # Track processor versions per commit
    commit_proc_versions: dict[str, dict[str, int]] = {}

    # Sort commits oldest-first for accumulation
    sorted_commits = sorted(commits, key=lambda c: c.get("date", ""))

    for proc in processors:
        proc: CommitProcessor
        job_store.add_log(job_id, "info",
                          f"  Running processor: {proc.label} (v{proc.version})")
        delta_batch: list[dict] = []
        acc_batch: list[dict] = []
        detected_count = 0
        processed_count = 0

        # Per-file accumulation — seed from prior state if available
        accumulated: dict[str, dict] = dict(
            prior_accumulated.get(proc.node_property, {}))

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

                # Record processor version for this commit
                commit_proc_versions.setdefault(
                    commit["hash"], {})[proc.name] = proc.version

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

    # Write processor versions per commit
    if commit_proc_versions:
        pv_batch = [
            {"hash": h, "versions": json.dumps(versions)}
            for h, versions in commit_proc_versions.items()
        ]
        for i in range(0, len(pv_batch), _BATCH_SIZE):
            chunk = pv_batch[i:i + _BATCH_SIZE]
            run_cypher_write(driver, _CYPHER_SET_COMMIT_PROC_VERSIONS,
                             {"items": chunk})

    return documented


async def _ingest_repo(driver, job_id: str, repo: dict, repo_name: str,
                       repo_path: str, branch_filter: list[str] | None,
                       mode: str) -> tuple[int, int, int]:
    """Ingest commits for a single repo. Returns (commits, issues, files)."""
    is_incremental = mode == "incremental"
    prior_state: dict | None = None
    prior_tips: dict[str, str] = {}
    prior_accumulated: dict[str, dict] | None = None

    if is_incremental:
        prior_state = await asyncio.to_thread(
            _load_ingestion_state, driver, repo_name)
        if prior_state is None:
            job_store.add_log(job_id, "info",
                              f"{repo_name}: no prior state, "
                              f"falling back to full ingestion")
            is_incremental = False
        else:
            prior_tips = prior_state.get("branch_tips", {})
            job_store.add_log(
                job_id, "info",
                f"{repo_name}: incremental from "
                f"{len(prior_tips)} known branch tip(s)")

    # Fetch latest remote refs (safe — does not touch working tree)
    job_store.add_log(job_id, "info",
                      f"{repo_name}: fetching remote refs...")
    await asyncio.to_thread(
        _fetch_remote_branches, repo_path, branch_filter, job_id)

    # Build git log command using origin/ remote tracking refs
    if is_incremental and prior_tips:
        git_cmd = _build_incremental_git_cmd(branch_filter, prior_tips)
        job_store.add_log(job_id, "info",
                          f"{repo_name}: fetching new commits only")
    else:
        git_cmd = ["git", "log"]
        if branch_filter:
            git_cmd += [f"origin/{b}" for b in branch_filter]
            job_store.add_log(job_id, "info",
                              f"{repo_name}: filtering by branches: "
                              f"{', '.join(branch_filter)}")
        else:
            git_cmd.append("--all")
        git_cmd += [f"--format={_GIT_LOG_FORMAT}", "--name-only"]

    # Run git log
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
        return 0, 0, 0
    except Exception as e:
        job_store.add_log(job_id, "warn",
                          f"{repo_name}: git log failed: {e}")
        return 0, 0, 0

    if proc.returncode != 0:
        # Incremental range may fail on force-pushed branches
        if is_incremental:
            job_store.add_log(
                job_id, "warn",
                f"{repo_name}: incremental git log failed "
                f"(possible force push), retrying full ingestion")
            return await _ingest_repo(
                driver, job_id, repo, repo_name, repo_path,
                branch_filter, mode="full")
        job_store.add_log(job_id, "warn",
                          f"{repo_name}: git log returned exit code "
                          f"{proc.returncode}: "
                          f"{(proc.stderr or '').strip()}")
        return 0, 0, 0

    # Parse
    commits = _parse_git_log_output(proc.stdout or "")
    total_available = len(commits)
    job_store.add_log(job_id, "info",
                      f"{repo_name}: parsed {total_available} "
                      f"{'new ' if is_incremental else ''}commits")

    # Chunk: take only the oldest N commits so we can test incremental
    chunked = False
    if _COMMIT_CHUNK_SIZE and len(commits) > _COMMIT_CHUNK_SIZE:
        # git log returns newest-first; reverse → oldest-first, slice, reverse back
        commits.reverse()
        commits = commits[:_COMMIT_CHUNK_SIZE]
        commits.reverse()
        chunked = True
        job_store.add_log(
            job_id, "info",
            f"{repo_name}: chunked to {len(commits)} oldest commits "
            f"(of {total_available})")

    # Full mode: clear existing data
    if not is_incremental:
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

    # Write commits (MERGE is idempotent)
    written = 0
    if commits:
        written = await asyncio.to_thread(
            _write_commits_batch, driver, repo_name, commits, job_id)
        await asyncio.to_thread(
            _write_parent_edges, driver, commits, job_id)

    # Discover and write branches with membership
    branches = await asyncio.to_thread(
        _discover_branches, repo_path)
    if branch_filter:
        branches = [b for b in branches if b["name"] in branch_filter]

    # Clean up stale branches (in prior state but no longer in git)
    if is_incremental and prior_tips:
        current_names = {b["name"] for b in branches}
        for old_name in prior_tips:
            if old_name not in current_names:
                job_store.add_log(job_id, "info",
                                  f"{repo_name}: removing stale branch "
                                  f"{old_name}")
                await asyncio.to_thread(
                    run_cypher_write, driver, _CYPHER_UNSTAMP_BRANCH,
                    {"repo_name": repo_name, "branch_name": old_name})
                await asyncio.to_thread(
                    run_cypher_write, driver, _CYPHER_REMOVE_STALE_BRANCH,
                    {"repo_name": repo_name, "branch_name": old_name})

    if branches:
        # When chunked, the real branch tips may not be in our commit set.
        # Override tips to the newest commit we actually processed so that
        # branch nodes point to an existing commit and walks stay in-bounds.
        if chunked and commits:
            newest_hash = commits[0]["hash"]  # commits[0] is newest
            commit_hashes = {c["hash"] for c in commits}
            for b in branches:
                if b["tip_hash"] not in commit_hashes:
                    b["tip_hash"] = newest_hash

        await asyncio.to_thread(
            run_cypher_write, driver, _CYPHER_BRANCHES,
            {"repo_name": repo_name, "branches": branches})
        # Stamp branch name on all reachable commits
        ap_map = _build_all_parents_map(commits)
        fp_map = _build_first_parent_map(commits)
        for b in branches:
            if is_incremental or chunked:
                # In incremental/chunked mode the full ancestor chain is
                # not in memory. Stamp all commits in this batch — they
                # are all reachable from the branch. Prior commits were
                # already stamped in earlier runs.
                all_hashes = [c["hash"] for c in commits]
                first_chain = _walk_first_parents(
                    b["tip_hash"], fp_map)
            else:
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
    unique_files: set[str] = set()
    for c in commits:
        unique_files.update(c["files_changed"])

    job_store.add_log(
        job_id, "info",
        f"{repo_name}: {written} commits, "
        f"{issue_count} issue refs, "
        f"{len(unique_files)} unique files")

    # ── Run commit processors (on new commits only) ──
    if not commits:
        job_store.add_log(job_id, "info",
                          f"{repo_name}: no new commits, "
                          f"skipping processors")
    else:
        documented: dict[str, set[str]] = {}

        # Load prior accumulated state for incremental mode
        all_processors = []

        # 1. Matured processors (configured per-repo)
        repo_processor_names = repo.get("processors", [])
        if repo_processor_names:
            from app.analyzers.commit_processors import get_processors_by_name
            matured = get_processors_by_name(repo_processor_names)
            all_processors.extend(matured)

        # 2. Incubating processors (global, from config)
        from app.config import load_config_decrypted
        from app.analyzers.commit_processors import get_incubating_processors
        app_config = load_config_decrypted()
        incubating = get_incubating_processors(app_config)
        all_processors.extend(incubating)

        if is_incremental and all_processors:
            prior_accumulated = await asyncio.to_thread(
                _load_prior_accumulated, driver, repo_name,
                all_processors)
            if prior_accumulated:
                job_store.add_log(
                    job_id, "info",
                    f"{repo_name}: seeding accumulated state from "
                    f"{len(prior_accumulated)} processor(s)")

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
                    repo_path, commits, job_id,
                    prior_accumulated=prior_accumulated)

        if incubating:
            job_store.add_log(
                job_id, "info",
                f"{repo_name}: running incubating processors: "
                f"{', '.join(p.label for p in incubating)}")
            documented = await asyncio.to_thread(
                _run_processors, driver, incubating,
                repo_path, commits, job_id, documented,
                prior_accumulated=prior_accumulated)

        # 3. Coverage checker (always last)
        from app.analyzers.commit_processors.coverage_checker import (
            run_coverage_check, suggest_new_processors)
        job_store.add_log(job_id, "info",
                          f"{repo_name}: running coverage check...")
        uncovered_map = run_coverage_check(
            commits, documented, job_id)
        if uncovered_map:
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

            suggestions = await asyncio.to_thread(
                suggest_new_processors, uncovered_map, job_id)
            if suggestions:
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

    # ── Save ingestion state ──
    if chunked and commits:
        # When chunked, save the newest processed commit as the tip
        # so the next incremental run continues from here
        newest_hash = commits[0]["full_hash"][:12]  # commits[0] is newest
        new_tips = {b["name"]: newest_hash for b in branches}
        job_store.add_log(
            job_id, "info",
            f"{repo_name}: chunked — saving checkpoint at {newest_hash}")
    else:
        new_tips = {b["name"]: b["tip_hash"] for b in branches}
    # Collect processor versions used
    all_proc_versions: dict[str, int] = {}
    repo_processor_names = repo.get("processors", [])
    if repo_processor_names:
        from app.analyzers.commit_processors import get_processors_by_name
        for p in get_processors_by_name(repo_processor_names):
            all_proc_versions[p.name] = p.version
    from app.config import load_config_decrypted
    from app.analyzers.commit_processors import get_incubating_processors
    app_config = load_config_decrypted()
    for p in get_incubating_processors(app_config):
        all_proc_versions[p.name] = p.version

    state = {
        "branch_tips": new_tips,
        "processor_versions": all_proc_versions,
        "last_ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    await asyncio.to_thread(
        _save_ingestion_state, driver, repo_name, state)
    job_store.add_log(job_id, "info",
                      f"{repo_name}: ingestion state saved")

    return written, issue_count, len(unique_files)


async def run_ingest_commits(job_id: str, repos: list[dict],
                             branch_filter: list[str] | None = None,
                             mode: str = "incremental"):
    """Main entry point: ingest git commits into Neo4j for given repos.

    Args:
        mode: "incremental" (default) fetches only new commits since last
              ingestion. "full" clears and re-ingests everything.
    """
    job_store.update_status(job_id, JobStatus.RUNNING)
    try:
        driver = get_neo4j_driver()

        # Ensure date index exists
        await asyncio.to_thread(
            run_cypher_write, driver, _CYPHER_INDEX_DATE)

        total_commits_all = 0
        total_issues_all = 0
        total_files_all = 0

        job_store.add_log(job_id, "info",
                          f"Ingestion mode: {mode}")

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

            written, issue_count, file_count = await _ingest_repo(
                driver, job_id, repo, repo_name, repo_path,
                branch_filter, mode)
            total_commits_all += written
            total_issues_all += issue_count
            total_files_all += file_count

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
