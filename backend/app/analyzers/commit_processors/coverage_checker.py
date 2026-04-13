"""Coverage checker — runs last, identifies uncovered files and suggests new processors."""

import json
from collections import defaultdict
from pathlib import PurePosixPath

from app.job_store import job_store


def _group_by_directory_and_extension(files: list[str]) -> list[dict]:
    """Group uncovered files by parent directory + extension pattern."""
    groups: dict[str, list[str]] = defaultdict(list)
    for f in files:
        p = PurePosixPath(f)
        ext = p.suffix or "(no ext)"
        # Use the deepest 2 directory segments for grouping
        parts = p.parent.parts
        dir_key = "/".join(parts[-2:]) if len(parts) >= 2 else str(p.parent)
        groups[f"{dir_key}/*{ext}"].append(f)

    return [
        {"pattern": pattern, "files": file_list, "count": len(file_list)}
        for pattern, file_list in sorted(groups.items(),
                                          key=lambda x: -len(x[1]))
    ]


def _suggest_processors_via_ai(file_groups: list[dict]) -> list[dict] | None:
    """Call AI to suggest processor names and purposes for file groups."""
    from app.config import load_config_decrypted

    config = load_config_decrypted()

    # Find AI provider
    provider = None
    for task_type in ("commit_processing", "repository_analysis"):
        task = next(
            (t for t in config.ai_tasks if t.task_type == task_type), None)
        if task:
            provider = next(
                (p for p in config.ai_providers
                 if p.name == task.provider_name), None)
            if provider:
                break
    if not provider and config.ai_providers:
        provider = config.ai_providers[0]
    if not provider:
        return None

    from openai import OpenAI
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)

    groups_text = ""
    for g in file_groups[:15]:  # limit to 15 groups
        sample = ", ".join(g["files"][:5])
        if len(g["files"]) > 5:
            sample += f", ... ({g['count']} total)"
        groups_text += f"  Pattern: {g['pattern']} — {sample}\n"

    system_prompt = (
        "You are a software documentation architect. Given groups of files "
        "from git commits that are not yet documented by any commit processor, "
        "suggest new commit processors that could document these file types.\n\n"
        "For each suggestion, provide:\n"
        '- "name": a short snake_case identifier (e.g. "spring_config", "sql_migrations")\n'
        '- "label": a human-readable display name\n'
        '- "description": what the processor would document\n'
        '- "instructions": detailed instructions for an AI to analyze these files\n'
        '- "file_patterns": array of regex patterns to match these files\n'
        '- "covers_groups": array of the input pattern strings this processor would cover\n\n'
        "Respond with a JSON array of suggestions. Only output valid JSON, no markdown fences. "
        "Group related file patterns into a single processor where it makes sense. "
        "Skip trivial files like .gitignore or IDE configs."
    )

    try:
        response = client.chat.completions.create(
            model=provider.default_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Uncovered file groups:\n{groups_text}"},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        text = (response.choices[0].message.content or "").strip()
        return json.loads(text)
    except Exception:
        return None


def run_coverage_check(commits: list[dict], documented: dict[str, set[str]],
                       job_id: str) -> dict[str, list[str]]:
    """Identify uncovered files per commit. Returns {hash: [uncovered_files]}.

    Does NOT call AI — just computes which files lack processor coverage.
    """
    uncovered_map: dict[str, list[str]] = {}
    total_uncovered = 0

    for commit in commits:
        files = commit.get("files_changed", [])
        if not files:
            continue
        doc_files = documented.get(commit["hash"], set())
        uncovered = [f for f in files if f not in doc_files]
        if uncovered:
            uncovered_map[commit["hash"]] = uncovered
            total_uncovered += len(uncovered)

    if uncovered_map:
        job_store.add_log(
            job_id, "info",
            f"  Coverage check: {len(uncovered_map)} commits have "
            f"{total_uncovered} uncovered files")

    return uncovered_map


def suggest_new_processors(uncovered_map: dict[str, list[str]],
                           job_id: str) -> list[dict]:
    """Group uncovered files and use AI to suggest new processors."""
    # Collect all uncovered files across commits
    all_uncovered: list[str] = []
    for files in uncovered_map.values():
        all_uncovered.extend(files)

    if not all_uncovered:
        return []

    # Deduplicate and group
    unique_files = sorted(set(all_uncovered))
    groups = _group_by_directory_and_extension(unique_files)

    if not groups:
        return []

    job_store.add_log(
        job_id, "info",
        f"  Coverage: {len(unique_files)} unique uncovered files in "
        f"{len(groups)} groups, requesting AI suggestions...")

    suggestions = _suggest_processors_via_ai(groups)
    if suggestions:
        job_store.add_log(
            job_id, "info",
            f"  AI suggested {len(suggestions)} new processor(s): "
            f"{', '.join(s.get('name', '?') for s in suggestions)}")
    else:
        job_store.add_log(
            job_id, "warn",
            "  Could not get AI suggestions (no provider or API error)")

    return suggestions or []
